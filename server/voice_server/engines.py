"""Local speech-to-text, LLM and text-to-speech engines."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime

import httpx
import mlx_whisper
import numpy as np
from piper import PiperVoice

from .audio import normalize, resample
from .config import Settings
from .tools import route_hint

log = logging.getLogger(__name__)

LLM_RETRIES = 3
LLM_BACKOFF_SECONDS = 0.5
LLM_TIMEOUT_SECONDS = 60.0
MAX_TOOL_ROUNDS = 2
# Example sentence in simplified Chinese with English loanwords and the wake word: keeps
# auto-detected Chinese simplified, English terms in Latin script, and spells the name 小奇
# (not 小琪). Benchmarked against forcing language="zh" (slow, garbled English sentences)
# and plain auto-detect (traditional characters).
ASR_PROMPT = "小奇小奇，帮我打开 GitHub 看看 iPhone 的 meeting 安排。"
ASR_LANGUAGES = ("zh", "en")
ASR_FALLBACK_LANGUAGE = "zh"


# MLX (Whisper and Qwen3-TTS) must not be driven from several threads at once.
MLX_LOCK = threading.Lock()


class Asr:
    def __init__(self, model: str, transcribe_fn: Callable[..., dict] = mlx_whisper.transcribe) -> None:
        self._model = model
        self._transcribe = transcribe_fn
        self._lock = MLX_LOCK

    def _run(self, audio: np.ndarray, language: str | None) -> dict:
        return self._transcribe(audio, path_or_hf_repo=self._model, language=language, initial_prompt=ASR_PROMPT,
                                condition_on_previous_text=False, verbose=None)

    def transcribe(self, audio: np.ndarray) -> str:
        """Auto-detects Chinese vs English; any other detected language (short clips are
        sometimes taken for e.g. Swahili) is re-decoded as Chinese."""
        with self._lock:
            result = self._run(audio, None)
            if result.get("language") not in ASR_LANGUAGES:
                log.info("asr detected %r, re-decoding as %s", result.get("language"), ASR_FALLBACK_LANGUAGE)
                result = self._run(audio, ASR_FALLBACK_LANGUAGE)
        return str(result.get("text", "")).strip()


ToolFn = Callable[..., str]


# Some reasoning models inline their chain of thought instead of using a separate field.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Qwen-family models sometimes write a tool call as text when tools are withheld.
_TEXT_TOOL_CALL = re.compile(r"<tool_call>.*?(</tool_call>|$)", re.DOTALL)
FINAL_ROUND_NUDGE = "请直接根据上面已有的搜索结果回答用户，不要再调用工具；没有查到就如实说。"
NO_ANSWER_REPLY = "[calm]抱歉，我没查到确切的信息。"
# Without thinking, models sometimes promise to look something up instead of calling the tool.
_STALL = re.compile(r"^\W*(\[\w+\])?\W*.{0,10}(查|搜|看)(一下|一查|查|搜|看)\W*$")
_EMOTION_ONLY = re.compile(r"^\W*\[\w+\]\W*$|^\W*$")
STALL_NUDGE = "不要只说要去查，现在就调用 web_search 工具获取信息，然后再回答。"


class Llm:
    """Chat client for either an OpenAI-compatible server (LM Studio) or Ollama.

    Both speak the same tool-spec format; they differ in endpoint, payload knobs,
    response envelope and how tool results reference their call.
    """

    def __init__(
        self,
        settings: Settings,
        tools: dict[str, tuple[dict, ToolFn]] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._s = settings
        self._openai = settings.llm_backend == "openai"
        self._client = client or httpx.Client(base_url=settings.llm_url, timeout=LLM_TIMEOUT_SECONDS)
        self._tools = tools or {}  # name -> (tool spec, implementation)

    def _system(self) -> str:
        """Static per configuration, so it caches (see DEFAULT_SYSTEM_PROMPT)."""
        return self._s.system_prompt.format(location=self._s.user_location)

    @staticmethod
    def _context(hint: str | None) -> str:
        now = datetime.now()
        text = now.strftime("现在是 %Y年%m月%d日 %H:%M，星期") + "一二三四五六日"[now.weekday()] + "。"
        return f"{text}{hint}" if hint else text

    def _payload(self, messages: list[dict], with_tools: bool) -> tuple[str, dict]:
        if self._openai:
            path = "/chat/completions"
            payload = {"model": self._s.llm_model, "messages": messages, "stream": False,
                       "max_tokens": self._s.llm_max_tokens}
            if self._s.llm_reasoning_effort:
                payload["reasoning_effort"] = self._s.llm_reasoning_effort
        else:
            path = "/api/chat"
            payload = {"model": self._s.llm_model, "messages": messages, "stream": False,
                       "keep_alive": self._s.ollama_keep_alive, "options": {"num_predict": self._s.llm_max_tokens}}
        if with_tools and self._tools:
            # Always the full, fixed list: varying it per round would break the prompt cache.
            payload["tools"] = [spec for spec, _ in self._tools.values()]
        return path, payload

    def _post(self, messages: list[dict], with_tools: bool) -> dict:
        """Returns the assistant message normalised to {"content": str, "tool_calls": list}."""
        path, payload = self._payload(messages, with_tools)
        last_err: Exception | None = None
        for attempt in range(LLM_RETRIES):
            try:
                resp = self._client.post(path, json=payload)
                resp.raise_for_status()
                body = resp.json()
                msg = body["choices"][0]["message"] if self._openai else body["message"]
                content = _TEXT_TOOL_CALL.sub("", _THINK.sub("", msg.get("content") or ""))
                return {"content": content, "tool_calls": msg.get("tool_calls") or []}
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                last_err = e
                log.warning("llm attempt %d failed: %s", attempt + 1, e)
                time.sleep(LLM_BACKOFF_SECONDS * 2**attempt)
        raise RuntimeError(f"llm unavailable ({self._s.llm_backend} {self._s.llm_url}): {last_err}")

    def _tool_result(self, call: dict, name: str, out: str) -> dict:
        if self._openai:
            return {"role": "tool", "tool_call_id": call.get("id", ""), "content": out}
        return {"role": "tool", "tool_name": name, "content": out}

    def _run_tool(self, call: dict) -> tuple[str, str]:
        fn = call.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if name not in self._tools:
            return name, f"未知工具：{name}"
        t = time.perf_counter()
        try:
            out = self._tools[name][1](**args)
        except TypeError as e:  # model passed bad arguments
            out = f"工具参数错误：{e}"
        log.info("tool %s(%s) -> %d chars in %.0fms", name, args, len(out), (time.perf_counter() - t) * 1000)
        return name, out

    def chat(self, history: list[dict[str, str]], user: str) -> tuple[str, list[str]]:
        """Returns (reply, names of tools used). Tools may run for up to MAX_TOOL_ROUNDS."""
        hint = route_hint(user, set(self._tools))
        messages: list[dict] = [{"role": "system", "content": self._system()}, *history,
                                {"role": "system", "content": self._context(hint)},
                                {"role": "user", "content": user}]
        used: list[str] = []
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            last = round_ == MAX_TOOL_ROUNDS
            if last and used:
                # Tools are withheld now; say so, or some models keep "calling" them in text.
                messages.append({"role": "system", "content": FINAL_ROUND_NUDGE})
            msg = self._post(messages, with_tools=not last)
            calls = msg["tool_calls"]
            # Nudge once when the model answered without a tool although the question needs
            # one (routing hint), or when it only promised to look something up.
            needs_tool = round_ == 0 and hint is not None
            content = msg["content"].strip()
            # A bare emotion tag ("[calm]") is no answer either.
            spoken = _EMOTION_ONLY.sub("", content)
            if not calls and not last and self._tools and (needs_tool or not spoken or _STALL.match(content)):
                log.info("llm answered %r without a tool; nudging it to call one", content)
                messages.append({"role": "assistant", "content": msg["content"]})
                messages.append({"role": "system", "content": STALL_NUDGE})
                msg = self._post(messages, with_tools=True)
                calls = msg["tool_calls"]
            if not calls or last:
                reply = msg["content"].strip()
                return (reply or (NO_ANSWER_REPLY if used else "")), used
            messages.append({"role": "assistant", "content": msg["content"], "tool_calls": calls})
            for call in calls:
                name, out = self._run_tool(call)
                used.append(name)
                messages.append(self._tool_result(call, name, out))
        return "", used


class PiperTts:
    """Fast, lightweight fallback voice; ignores emotion."""

    def __init__(self, voice_path: str, out_rate: int) -> None:
        self._voice = PiperVoice.load(voice_path)
        self._out_rate = out_rate
        self._lock = threading.Lock()

    def synthesize(self, text: str, emotion: str | None = None) -> np.ndarray:
        with self._lock:
            chunks = list(self._voice.synthesize(text))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        pcm = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in chunks])
        audio = pcm.astype(np.float32) / 32768.0
        return normalize(resample(audio, chunks[0].sample_rate, self._out_rate))


# Delivery instructions for Qwen3-TTS CustomVoice, keyed by the reply's emotion tag.
EMOTION_INSTRUCT = {
    "happy": "用非常开心、兴奋、活泼的语气说",
    "surprised": "用惊讶、好奇的语气说",
    "sad": "用温柔、心疼、轻声安慰的语气说",
    "shy": "用害羞、不好意思的语气说",
    "smug": "用调皮、得意的语气说",
}


class QwenTts:
    """Qwen3-TTS via mlx-audio: natural Mandarin with English terms, emotion-aware delivery."""

    def __init__(self, model: str, voice: str, out_rate: int, load: Callable[[str], object] | None = None) -> None:
        if load is None:
            from mlx_audio.tts.utils import load_model as load  # heavy import; only when used
        self._model = load(model)
        self._voice = voice
        self._out_rate = out_rate
        self._lock = MLX_LOCK

    def synthesize(self, text: str, emotion: str | None = None) -> np.ndarray:
        instruct = EMOTION_INSTRUCT.get(emotion or "")
        with self._lock:
            results = list(self._model.generate(text=text, voice=self._voice, lang_code="chinese", instruct=instruct))
        if not results:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate([np.array(r.audio, dtype=np.float32).reshape(-1) for r in results])
        return normalize(resample(audio, results[0].sample_rate, self._out_rate))
