import numpy as np
import pytest

from voice_server.audio import float_to_pcm16, normalize, pcm16_to_float, resample, rms_int16
from voice_server.sessions import AudioStore, SessionStore
from voice_server.text import clean_reply, clean_transcript, split_emotion, split_wake_word, to_speech


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_pcm_roundtrip_and_odd_length():
    pcm = np.array([0, 1000, -1000, 32767], dtype="<i2").tobytes()
    back = float_to_pcm16(pcm16_to_float(pcm + b"\x00"))  # trailing odd byte is dropped
    assert np.allclose(np.frombuffer(back, "<i2"), [0, 1000, -1000, 32766], atol=1)


def test_rms_empty_and_constant():
    assert rms_int16(b"") == 0.0
    assert rms_int16(np.full(100, 300, "<i2").tobytes()) == pytest.approx(300)


@pytest.mark.parametrize("src,dst", [(22050, 16000), (16000, 16000), (8000, 16000)])
def test_resample_length(src, dst):
    audio = np.sin(np.linspace(0, 100, src)).astype(np.float32)  # one second
    out = resample(audio, src, dst)
    assert out.size == dst and out.dtype == np.float32


def test_normalize_silence_untouched():
    z = np.zeros(10, np.float32)
    assert np.array_equal(normalize(z), z)
    assert np.max(np.abs(normalize(np.array([0.1, -0.2], np.float32)))) == pytest.approx(0.9)


@pytest.mark.parametrize(
    "raw,expected",
    [("谢谢观看！", ""), ("请不吝点赞 订阅 转发", ""), ("请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", ""), ("帮我查一下怎么订阅这个频道的更新", "帮我查一下怎么订阅这个频道的更新"), ("  你好 ", "你好")],
)
def test_clean_transcript(raw, expected):
    assert clean_transcript(raw) == expected


def test_clean_reply_strips_markup_and_emoji():
    assert clean_reply("**好的**😀\n- 第一点\n1. 第二点") == "好的 第一点 第二点"


def test_session_history_trims_and_expires():
    clock = FakeClock()
    store = SessionStore(max_turns=2, idle_seconds=10, clock=clock)
    for i in range(3):
        store.append("dev", f"u{i}", f"a{i}")
    assert [m["content"] for m in store.history("dev")] == ["u1", "a1", "u2", "a2"]
    clock.t = 11
    assert store.history("dev") == []
    store.append("dev", "x", "y")  # an idle session restarts instead of growing
    assert len(store.history("dev")) == 2


def test_audio_store_ttl():
    clock = FakeClock()
    store = AudioStore(ttl_seconds=5, clock=clock)
    key = store.put(b"abc")
    assert store.get(key) == b"abc"
    clock.t = 6
    assert store.get(key) is None
    assert store.get("missing") is None


@pytest.mark.parametrize(
    "raw,spoken",
    [("气温17到21℃", "气温17到21度"), ("湿度60%", "湿度百分之60"), ("风速12 km/h", "风速12公里每小时"),
     ("18~25°C", "18到25度"), ("跌了 -1.5%", "跌了 负百分之1.5"), ("涨+2.3%", "涨百分之2.3"), ("2026-10-23", "2026年10月23日"),
     ("10-20度", "10到20度")],
)
def test_to_speech(raw, spoken):
    assert to_speech(raw) == spoken


@pytest.mark.parametrize(
    "raw,emotion,rest",
    [("[happy]太棒了", "happy", "太棒了"), ("  【sad】 好难过", "sad", "好难过"), ("(Smug)嘿嘿", "smug", "嘿嘿"),
     ("[angry]哼", "calm", "哼"), ("[excited]哇", "happy", "哇"), ("没有标签", "calm", "没有标签"), ("[happy]", "happy", "")],
)
def test_split_emotion(raw, emotion, rest):
    assert split_emotion(raw) == (emotion, rest)


@pytest.mark.parametrize(
    "text,woken,rest",
    [("小奇小奇，明天下雨吗？", True, "明天下雨吗？"), ("小琪小琪", True, ""), ("晓齐，讲个笑话", True, "讲个笑话"),
     ("嘿，小奇，现在几点", True, "现在几点"), ("小七 小七!", True, ""), ("今天小奇真可爱", False, "今天小奇真可爱"),
     ("你吃饭了吗", False, "你吃饭了吗"), ("", False, ""), ("小奇怪的事情", False, "小奇怪的事情"),
     ("小奇迹", False, "小奇迹"), ("小奇小奇明天下雨吗", True, "明天下雨吗"), ("小奇", True, "")],
)
def test_split_wake_word(text, woken, rest):
    assert split_wake_word(text) == (woken, rest)


def test_clean_transcript_drops_replacement_chars():
    assert clean_transcript("小琪小琪�a明天") == "小琪小琪a明天"


def test_asr_redecodes_unexpected_language_as_chinese():
    from voice_server.engines import Asr

    calls = []

    def fake(audio, language=None, **_):
        calls.append(language)
        return {"language": "sw", "text": "garbage"} if language is None else {"language": "zh", "text": "讲个笑话"}

    assert Asr("m", transcribe_fn=fake).transcribe(np.zeros(10, np.float32)) == "讲个笑话"
    assert calls == [None, "zh"]


def test_asr_keeps_english_without_retry():
    from voice_server.engines import Asr

    calls = []

    def fake(audio, language=None, **_):
        calls.append(language)
        return {"language": "en", "text": "Tell me a joke"}

    assert Asr("m", transcribe_fn=fake).transcribe(np.zeros(10, np.float32)) == "Tell me a joke"
    assert calls == [None]


def test_qwen_tts_maps_emotion_to_instruction_and_resamples():
    from voice_server.engines import EMOTION_INSTRUCT, QwenTts

    seen = {}

    class Result:
        def __init__(self):
            self.audio = np.full(2400, 0.25, np.float32)  # 0.1 s at 24 kHz
            self.sample_rate = 24000

    class FakeModel:
        def generate(self, **kw):
            seen.update(kw)
            yield Result()

    tts = QwenTts("m", "vivian", 16000, load=lambda _: FakeModel())
    out = tts.synthesize("你好", "sad")
    assert seen["instruct"] == EMOTION_INSTRUCT["sad"] and seen["voice"] == "vivian"
    assert out.size == 1600 and np.max(np.abs(out)) == pytest.approx(0.9)
    tts.synthesize("你好", "calm")
    assert seen["instruct"] is None
