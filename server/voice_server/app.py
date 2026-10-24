"""HTTP API for the ES3C28P voice chat board.

POST /api/chat        body: 16 kHz mono int16 LE PCM. Headers: X-Token, X-Device-Id.
                      -> REPLY = {"user", "reply", "emotion", "audio_url", "audio_bytes", "tools", "timing_ms"}
                      X-Hands-Free: 1 -> nothing heard yields an empty reply and no audio,
                      so an always-listening board stays quiet on noise.
                      A leading wake word ("小奇…") is stripped before the LLM sees the text.
POST /api/transcribe  body: PCM -> {"text", "woken", "rest", "timing_ms"}; ASR only, used by the
                      board's wake-word mode to decide cheaply whether it was addressed.
POST /api/chat_text   body: {"text": str} -> REPLY; empty text means "only the wake word was
                      said" and gets a short acknowledgement.
GET  /api/audio/{id}  -> reply speech, same PCM format
GET  /api/weather     {city, temp, code, text, icon, min, max} for the board's clock card
POST /api/reset       clears the device's conversation history
GET  /health
"""
from __future__ import annotations

import hmac
import logging
import time
import wave
from datetime import datetime
from contextlib import asynccontextmanager

import numpy as np
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .audio import SILENCE_RMS, float_to_pcm16, pcm16_to_float, rms_int16
from .config import Settings
from .engines import Asr, Llm, PiperTts, QwenTts
from .sessions import AudioStore, SessionStore
from .text import DEFAULT_EMOTION, clean_reply, clean_transcript, split_emotion, split_wake_word, to_speech
from .tools import (NEWS_SPEC, STOCK_SPEC, WEATHER_SPEC, WEB_SEARCH_SPEC, StockQuoteTool, TechNewsTool,
                    WeatherTool, WebSearch)

log = logging.getLogger("voice_server")

MIN_RECORD_SECONDS = 0.3
NOT_HEARD_REPLY = "我没听清，能再说一遍吗？"
WAKE_ACK_REPLY = "我在呢，你说～"
WAKE_ACK_EMOTION = "happy"


# A spoken question is a sentence or two; this only bounds abuse of the text endpoint.
MAX_CHAT_TEXT_CHARS = 2000


class ChatText(BaseModel):
    text: str = Field(default="", max_length=MAX_CHAT_TEXT_CHARS)


def _ms(timing: dict[str, float]) -> dict[str, int]:
    return {k: round(v) for k, v in timing.items()}


def create_app(settings: Settings | None = None, engines: dict | None = None) -> FastAPI:
    """engines: optional {"asr", "llm", "tts"} overrides (tests); otherwise real models load at startup."""
    s = settings or Settings()
    state: dict = dict(engines or {})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if engines is not None:
            yield
            return
        voice = f"qwen3:{s.qwen_tts_voice}" if s.tts_engine == "qwen3" else f"piper:{s.piper_voice.name}"
        log.info("loading models whisper=%s llm=%s/%s tts=%s", s.whisper_model, s.llm_backend, s.llm_model, voice)
        state["asr"] = Asr(s.whisper_model)
        weather, news = WeatherTool(s.user_location), TechNewsTool()
        state["weather"] = weather
        news.start_background_refresh()
        state["llm"] = Llm(s, tools={
            "get_weather": (WEATHER_SPEC, weather),
            "get_stock_quote": (STOCK_SPEC, StockQuoteTool()),
            "get_tech_news": (NEWS_SPEC, news),
            "web_search": (WEB_SEARCH_SPEC, WebSearch(s.searxng_url)),
        })
        if s.tts_engine == "qwen3":
            state["tts"] = QwenTts(s.qwen_tts_model, s.qwen_tts_voice, s.sample_rate)
        else:
            state["tts"] = PiperTts(str(s.piper_voice), s.sample_rate)
        # Warm up so the first real request isn't paying for model loads.
        t = time.perf_counter()
        state["asr"].transcribe(np.zeros(s.sample_rate, dtype=np.float32))
        state["llm"].chat([], "你好")
        state["tts"].synthesize("你好")
        weather()  # warms the geocode + forecast cache for the user's city
        log.info("models ready in %.1fs", time.perf_counter() - t)
        yield

    app = FastAPI(title="ES3C28P voice chat", lifespan=lifespan)
    sessions = SessionStore(s.history_turns, s.session_idle_seconds)
    audio_store = AudioStore(s.audio_ttl_seconds)

    def check_token(token: str | None) -> None:
        if s.token and not hmac.compare_digest((token or "").encode(), s.token.encode()):
            raise HTTPException(status_code=401, detail="bad token")

    max_pcm_bytes = int((s.max_record_seconds + 1) * s.sample_rate * 2)

    async def read_pcm(request: Request) -> tuple[bytes, float]:
        # Reject oversized uploads before buffering them: check the declared length, then cap the stream.
        too_long = HTTPException(status_code=413, detail="recording too long")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_pcm_bytes:
            raise too_long
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > max_pcm_bytes:
                raise too_long
        pcm = bytes(buf)
        return pcm, len(pcm) / 2 / s.sample_rate

    def save_clip(pcm: bytes, tag: str) -> None:
        """Keeps empty-result clips for diagnosis (DEBUG_CLIPS_DIR, newest DEBUG_CLIPS_MAX)."""
        if not s.debug_clips_dir:
            return
        d = Path(s.debug_clips_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{tag}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(s.sample_rate)
            w.writeframes(pcm)
        for old in sorted(d.glob("*.wav"))[: -s.debug_clips_max]:
            old.unlink()

    def transcribe(pcm: bytes, seconds: float, timing: dict[str, float]) -> str:
        t = time.perf_counter()
        user = raw = ""
        rms = rms_int16(pcm)
        gated = seconds < MIN_RECORD_SECONDS or rms < SILENCE_RMS
        if not gated:
            raw = state["asr"].transcribe(pcm16_to_float(pcm))
            user = clean_transcript(raw)
        timing["asr"] = (time.perf_counter() - t) * 1000
        log.info("asr secs=%.1f rms=%.0f gated=%s raw=%r text=%r", seconds, rms, gated, raw, user)
        if not user:
            save_clip(pcm, f"rms{rms:.0f}-{'gated' if gated else 'empty'}")
        return user

    def speak(text: str, emotion: str, timing: dict[str, float]) -> tuple[str, int]:
        t = time.perf_counter()
        pcm = float_to_pcm16(state["tts"].synthesize(to_speech(text), emotion))
        timing["tts"] = (time.perf_counter() - t) * 1000
        return audio_store.put(pcm), len(pcm)

    def reply_payload(user: str, reply: str, emotion: str, tools: list[str], timing: dict[str, float]) -> dict:
        audio_id, n_bytes = speak(reply, emotion, timing)
        return {
            "user": user,
            "reply": reply,
            "emotion": emotion,
            "audio_url": f"/api/audio/{audio_id}",
            "audio_bytes": n_bytes,
            "tools": tools,
            "timing_ms": _ms(timing),
        }

    def respond(user: str, device: str, timing: dict[str, float]) -> dict:
        """LLM + TTS for a transcript; an empty transcript means 'not heard'."""
        if not user:
            return reply_payload("", NOT_HEARD_REPLY, DEFAULT_EMOTION, [], timing)
        t = time.perf_counter()
        raw, tools_used = state["llm"].chat(sessions.history(device), user)
        emotion, raw = split_emotion(raw)
        reply = clean_reply(raw) or NOT_HEARD_REPLY
        timing["llm"] = (time.perf_counter() - t) * 1000
        sessions.append(device, user, reply)
        out = reply_payload(user, reply, emotion, tools_used, timing)
        log.info("chat device=%s user=%r reply=%r emotion=%s tools=%s timing_ms=%s",
                 device, user, reply, emotion, tools_used, out["timing_ms"])
        return out

    def quiet(timing: dict[str, float]) -> dict:
        return {"user": "", "reply": "", "emotion": DEFAULT_EMOTION, "audio_url": "", "audio_bytes": 0,
                "tools": [], "timing_ms": _ms(timing)}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "llm": f"{s.llm_backend}:{s.llm_model}"}

    @app.post("/api/chat")
    async def chat(
        request: Request,
        x_token: str | None = Header(default=None),
        x_device_id: str = Header(default="default"),
        x_hands_free: str = Header(default="0"),
    ) -> dict:
        check_token(x_token)
        pcm, seconds = await read_pcm(request)

        # Model calls are blocking; keep them off the event loop.
        def run() -> dict:
            timing: dict[str, float] = {}
            user = transcribe(pcm, seconds, timing)
            if not user and x_hands_free == "1":
                log.info("hands-free noise ignored device=%s secs=%.1f", x_device_id, seconds)
                return quiet(timing)
            woken, rest = split_wake_word(user)
            if woken and not rest:
                return reply_payload(user, WAKE_ACK_REPLY, WAKE_ACK_EMOTION, [], timing)
            return respond(rest if woken else user, x_device_id, timing)

        return await run_in_threadpool(run)

    @app.post("/api/transcribe")
    async def transcribe_only(request: Request, x_token: str | None = Header(default=None),
                              x_device_id: str = Header(default="default")) -> dict:
        check_token(x_token)
        pcm, seconds = await read_pcm(request)

        def run() -> dict:
            timing: dict[str, float] = {}
            text = transcribe(pcm, seconds, timing)
            woken, rest = split_wake_word(text)
            log.info("transcribe device=%s secs=%.1f text=%r woken=%s", x_device_id, seconds, text, woken)
            return {"text": text, "woken": woken, "rest": rest, "timing_ms": _ms(timing)}

        return await run_in_threadpool(run)

    @app.post("/api/chat_text")
    async def chat_text(body: ChatText, x_token: str | None = Header(default=None),
                        x_device_id: str = Header(default="default")) -> dict:
        check_token(x_token)

        def run() -> dict:
            timing: dict[str, float] = {}
            text = body.text.strip()
            if not text:
                return reply_payload("", WAKE_ACK_REPLY, WAKE_ACK_EMOTION, [], timing)
            return respond(text, x_device_id, timing)

        return await run_in_threadpool(run)

    @app.get("/api/weather")
    def weather_now(x_token: str | None = Header(default=None)) -> dict:
        """Current conditions in USER_LOCATION for the board's clock card."""
        check_token(x_token)
        snap = state["weather"].snapshot() if "weather" in state else None
        if snap is None:
            raise HTTPException(status_code=503, detail="weather unavailable")
        return snap

    @app.get("/api/audio/{audio_id}")
    def audio(audio_id: str, x_token: str | None = Header(default=None)) -> Response:
        check_token(x_token)
        pcm = audio_store.get(audio_id)
        if pcm is None:
            raise HTTPException(status_code=404, detail="audio expired")
        return Response(content=pcm, media_type="application/octet-stream")

    @app.post("/api/reset")
    def reset(x_token: str | None = Header(default=None), x_device_id: str = Header(default="default")) -> dict:
        check_token(x_token)
        sessions.reset(x_device_id)
        return {"ok": True}

    return app
