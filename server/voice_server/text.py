"""Text cleanup between the models and the board."""
from __future__ import annotations

import re

# Whisper emits these on silence, tones or noise. Strong markers never occur in
# real requests, so any transcript containing one is dropped outright.
_STRONG_HALLUCINATIONS = (
    "明镜与点点",
    "请不吝点赞",
    "点赞订阅",
    "打赏支持",
    "字幕由",
    "以下是普通话的句子",
)
# Weak markers can be genuine words, so only short transcripts dominated by them are dropped.
_HALLUCINATIONS = (
    "谢谢观看",
    "感谢观看",
    "谢谢大家",
    "订阅",
    "字幕",
)

_MARKDOWN = re.compile(r"[*_`#>~|]+")
_LIST_PREFIX = re.compile(r"^\s*(?:[-•]|\d+[.、)])\s*", re.MULTILINE)
# Anything outside BMP (emoji etc.) plus common symbol blocks the board font lacks.
_UNRENDERABLE = re.compile("[\U00010000-\U0010ffff☀-➿️‍]")
_SPACES = re.compile(r"\s+")


_PUNCT = re.compile(r"[\s，。！？、,.!?…]+")
# A hallucination is a short transcript dominated by one of the phrases; longer
# utterances that merely mention e.g. "订阅" are kept.
_HALLUCINATION_SLACK = 6


def clean_transcript(text: str) -> str:
    # Whisper occasionally emits U+FFFD where it split a multi-byte character.
    text = text.replace("\ufffd", "").strip()
    bare = _PUNCT.sub("", text)
    if any(h in bare for h in _STRONG_HALLUCINATIONS):
        return ""
    for h in _HALLUCINATIONS:
        if h in bare and len(bare) <= len(h) + _HALLUCINATION_SLACK:
            return ""
    return text


def clean_reply(text: str) -> str:
    """Strip markup/emoji so the reply both renders on the board and reads well aloud."""
    text = _LIST_PREFIX.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = _UNRENDERABLE.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    return text


# Symbols Piper skips or mispronounces, rewritten into words for TTS only.
_SPEECH_SUBS = (
    # Dates first, so the numeric-range rule below doesn't turn 2026-10-23 into "2026到10到23".
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), r"\1年\2月\3日"),
    (re.compile(r"(?<![\d\w])-(\d+(?:\.\d+)?)\s*%"), r"负百分之\1"),
    (re.compile(r"\+?(\d+(?:\.\d+)?)\s*%"), r"百分之\1"),
    (re.compile(r"\s*(?:℃|°C|摄氏度)"), "度"),
    (re.compile(r"\s*km/h", re.IGNORECASE), "公里每小时"),
    (re.compile(r"(\d)\s*[~～]\s*(\d)"), r"\1到\2"),
    (re.compile(r"(\d)\s*-\s*(\d)"), r"\1到\2"),
)


def to_speech(text: str) -> str:
    """Rewrites a display reply into text Piper reads naturally."""
    for pattern, repl in _SPEECH_SUBS:
        text = pattern.sub(repl, text)
    return text


EMOTIONS = ("happy", "surprised", "sad", "shy", "smug", "calm")
DEFAULT_EMOTION = "calm"
# Tags models invent despite the prompt, folded onto the six faces the board has.
_EMOTION_ALIASES = {
    "excited": "happy", "joy": "happy", "joyful": "happy", "cheerful": "happy", "proud": "smug",
    "playful": "smug", "naughty": "smug", "worried": "sad", "sorry": "sad", "sympathetic": "sad",
    "shocked": "surprised", "amazed": "surprised", "embarrassed": "shy", "neutral": "calm",
}
_EMOTION_TAG = re.compile(r"^\s*[\[【(（]\s*([a-zA-Z]+)\s*[\]】)）]\s*")


def split_emotion(text: str) -> tuple[str, str]:
    """Strips the leading [emotion] tag the LLM is asked to add; unknown or missing -> calm."""
    m = _EMOTION_TAG.match(text)
    if not m:
        return DEFAULT_EMOTION, text
    tag = m.group(1).lower()
    tag = _EMOTION_ALIASES.get(tag, tag)
    return (tag if tag in EMOTIONS else DEFAULT_EMOTION), text[m.end():]


# "小奇" as Whisper tends to spell it: 小/晓 + a same-sounding qi character, said once or twice,
# optionally after a greeting. Anything after the wake word is the actual request.
_QI = "奇琪齐棋旗七祺骑其淇麒"
_SEP = r"[\s，。,.!！?？、~～]"
# Said twice, anything may follow ("小奇小奇明天下雨吗"); said once it must be followed by a
# pause or the end, so words like "小奇怪" / "小奇迹" don't wake it.
_WAKE = re.compile(
    rf"^{_SEP}*(?:(?:嘿|喂|哈喽|你好){_SEP}*)?"
    rf"(?:[小晓][{_QI}]{_SEP}*[小晓][{_QI}]|[小晓][{_QI}](?={_SEP}|$))"
)
_LEADING_PUNCT = re.compile(r"^[\s，。,.!！?？、~～:：]+")


def split_wake_word(text: str) -> tuple[bool, str]:
    """Returns (woken, request text after the wake word)."""
    m = _WAKE.match(text)
    if not m:
        return False, text.strip()
    return True, _LEADING_PUNCT.sub("", text[m.end():]).strip()
