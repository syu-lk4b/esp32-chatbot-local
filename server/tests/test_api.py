import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_server.app import NOT_HEARD_REPLY, WAKE_ACK_REPLY, create_app
from voice_server.config import Settings

TOKEN = "t0k"
SPEECH = (np.sin(np.arange(16000) / 5) * 3000).astype("<i2").tobytes()  # 1 s, rms well above silence
SILENCE = np.zeros(16000, "<i2").tobytes()


class FakeAsr:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio):
        return self.text


class FakeLlm:
    def chat(self, history, user):
        return f"[happy]回答：{user}", []


class FakeTts:
    def synthesize(self, text, emotion=None):
        return np.zeros(1600, np.float32)


@pytest.fixture
def make_client():
    def _make(asr_text: str) -> TestClient:
        engines = {"asr": FakeAsr(asr_text), "llm": FakeLlm(), "tts": FakeTts()}
        app = create_app(Settings(token=TOKEN, debug_clips_dir=""), engines=engines)
        return TestClient(app)
    return _make


def post(client, pcm, hands_free=False):
    headers = {"X-Token": TOKEN, "X-Device-Id": "dev"}
    if hands_free:
        headers["X-Hands-Free"] = "1"
    return client.post("/api/chat", content=pcm, headers=headers)


def test_chat_returns_reply_and_fetchable_audio(make_client):
    with make_client("你好") as c:
        r = post(c, SPEECH).json()
        assert r["user"] == "你好" and r["reply"] == "回答：你好" and r["emotion"] == "happy"
        audio = c.get(r["audio_url"], headers={"X-Token": TOKEN})
        assert audio.status_code == 200 and len(audio.content) == r["audio_bytes"] == 3200


def test_hold_mode_says_not_heard_on_silence(make_client):
    with make_client("") as c:
        r = post(c, SILENCE).json()
        assert r["reply"] == NOT_HEARD_REPLY and r["audio_url"]


def test_hands_free_stays_quiet_on_noise(make_client):
    with make_client("谢谢观看") as c:  # hallucination on noise is filtered to empty
        r = post(c, SPEECH, hands_free=True).json()
        assert r["user"] == "" and r["reply"] == "" and r["audio_url"] == ""


def test_bad_token_rejected(make_client):
    with make_client("x") as c:
        assert c.post("/api/chat", content=SPEECH, headers={"X-Token": "no"}).status_code == 401


def test_transcribe_reports_wake_word(make_client):
    with make_client("小奇小奇，明天下雨吗") as c:
        r = c.post("/api/transcribe", content=SPEECH, headers={"X-Token": TOKEN}).json()
        assert r["woken"] is True and r["rest"] == "明天下雨吗"
    with make_client("我们去吃饭吧") as c:
        r = c.post("/api/transcribe", content=SPEECH, headers={"X-Token": TOKEN}).json()
        assert r["woken"] is False


def test_chat_text_answers_or_acknowledges(make_client):
    with make_client("unused") as c:
        r = c.post("/api/chat_text", json={"text": "明天下雨吗"}, headers={"X-Token": TOKEN}).json()
        assert r["reply"] == "回答：明天下雨吗" and r["audio_url"]
        ack = c.post("/api/chat_text", json={"text": ""}, headers={"X-Token": TOKEN}).json()
        assert ack["reply"] == WAKE_ACK_REPLY and ack["emotion"] == "happy" and ack["audio_url"]


def test_chat_strips_wake_word_before_llm(make_client):
    with make_client("小奇小奇，讲个笑话") as c:
        assert post(c, SPEECH).json()["reply"] == "回答：讲个笑话"
    with make_client("小奇小奇") as c:
        assert post(c, SPEECH).json()["reply"] == WAKE_ACK_REPLY


def test_weather_endpoint(make_client):
    class FakeWeather:
        def snapshot(self):
            return {"city": "上海", "temp": 21, "code": 81, "text": "阵雨", "icon": "rain", "min": 17, "max": 22}
    app = create_app(Settings(token=TOKEN, debug_clips_dir=""), engines={"asr": FakeAsr(""), "llm": FakeLlm(), "tts": FakeTts(), "weather": FakeWeather()})
    with TestClient(app) as c:
        assert c.get("/api/weather", headers={"X-Token": TOKEN}).json()["icon"] == "rain"
        assert c.get("/api/weather", headers={"X-Token": "no"}).status_code == 401
    with make_client("x") as c:  # no weather engine configured
        assert c.get("/api/weather", headers={"X-Token": TOKEN}).status_code == 503


def test_oversized_recording_rejected(make_client):
    too_long = b"\0" * (2 * 16000 * 22)  # 22 s > max_record_seconds + 1
    with make_client("x") as c:
        r = c.post("/api/chat", content=too_long, headers={"X-Token": TOKEN})
        assert r.status_code == 413


def test_oversized_recording_rejected_without_content_length(make_client):
    def chunks():  # chunked upload: no Content-Length, so the stream itself must be capped
        for _ in range(22):
            yield b"\0" * (2 * 16000)
    with make_client("x") as c:
        r = c.post("/api/transcribe", content=chunks(), headers={"X-Token": TOKEN})
        assert r.status_code == 413


def test_chat_text_length_capped(make_client):
    with make_client("x") as c:
        r = c.post("/api/chat_text", json={"text": "长" * 2001}, headers={"X-Token": TOKEN})
        assert r.status_code == 422


def test_lan_bind_requires_token():
    with pytest.raises(SystemExit, match="VOICE_TOKEN"):
        Settings(host="0.0.0.0", token="").require_safe_exposure()
    Settings(host="127.0.0.1", token="").require_safe_exposure()  # loopback-only is fine
    Settings(host="0.0.0.0", token=TOKEN).require_safe_exposure()
