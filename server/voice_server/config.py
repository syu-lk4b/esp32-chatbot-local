"""Runtime settings, read from environment variables (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SYSTEM_PROMPT = (
    "你是一个放在桌面上的语音聊天机器人，名字叫小奇，是一只可爱的哈士奇。"
    "用户通过语音和你对话，你的回答会被合成语音播放，同时显示在一块 2.8 寸小屏幕上。"
    "请用自然、口语化的简体中文回答，一般不超过 60 个字，除非用户明确要求详细说明。"
    "不要使用 Markdown、列表、表情符号或特殊符号，数字和单位用适合朗读的方式表达。"
    "遇到实时或你不确定的信息要先调用工具再回答：天气用 get_weather，股票、指数行情用 get_stock_quote，"
    "科技数码新闻用 get_tech_news，其他新闻、事实、价格等用 web_search。根据结果简短回答，不要念网址。"
    "需要搜索时直接调用工具，不要先回复“我查一下”之类的话。如果结果和问题不相关，换个更短的关键词再搜一次。搜索结果里没有的具体数据不要编造，搜不到就如实说。"
    "用户的话来自语音识别，可能有同音错字，比如“授盘”其实是“收盘”，请按最合理的意思理解，不必指出错字。"
    "每次回答的最开头必须加一个情绪标签，从 [happy] [surprised] [sad] [shy] [smug] [calm] 中选最贴合你回答语气的一个，"
    "例如“[happy]太棒了，恭喜你！”。标签只出现一次，后面才是正文。"
    "用户遇到开心的事用 [happy]；生病、难过、遇到坏事时用 [sad] 表示心疼；听到意外的消息用 [surprised]；"
    "被夸奖时用 [shy]；开玩笑、调皮时用 [smug]；普通问答用 [calm]。"
    "用户在{location}，问天气、新闻等和地点有关的问题时默认就是{location}，不要反问用户在哪。"
)
# The current time goes in a late message, not the system prompt, so the prompt prefix
# (system + tool specs + earlier turns) stays byte-identical and LM Studio's prompt cache
# keeps hitting: a cached prefix answers in ~0.5 s, a changed one costs ~2 s to re-read.


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: _env("VOICE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("VOICE_PORT", "8765")))
    # Shared secret the board sends in X-Token; empty disables the check.
    token: str = field(default_factory=lambda: _env("VOICE_TOKEN", ""))

    # "openai": any OpenAI-compatible server (LM Studio default); "ollama": Ollama's native API.
    llm_backend: str = field(default_factory=lambda: _env("LLM_BACKEND", "openai"))
    llm_url: str = field(default_factory=lambda: _env("LLM_URL", "http://127.0.0.1:1234/v1"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen3-30b-a3b-instruct-2507"))
    # OpenAI backend only: sent as reasoning_effort when set. "none" turns off thinking on
    # LM Studio reasoning models (2-3x faster replies); empty leaves the model default.
    llm_reasoning_effort: str = field(default_factory=lambda: _env("LLM_REASONING_EFFORT", ""))
    ollama_keep_alive: str = field(default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "30m"))
    # Reasoning tokens count against this budget on thinking models, so leave headroom.
    llm_max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "600")))
    system_prompt: str = field(default_factory=lambda: _env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT))
    user_location: str = field(default_factory=lambda: _env("USER_LOCATION", "上海"))

    searxng_url: str = field(default_factory=lambda: _env("SEARXNG_URL", "http://127.0.0.1:8888"))

    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"))
    # "qwen3": Qwen3-TTS (natural, emotion-aware, ~1 s per sentence); "piper": fast fallback.
    tts_engine: str = field(default_factory=lambda: _env("TTS_ENGINE", "qwen3"))
    qwen_tts_model: str = field(default_factory=lambda: _env("QWEN_TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"))
    qwen_tts_voice: str = field(default_factory=lambda: _env("QWEN_TTS_VOICE", "vivian"))
    piper_voice: Path = field(
        default_factory=lambda: Path(_env("PIPER_VOICE", str(SERVER_ROOT / "models" / "zh_CN-huayan-medium.onnx")))
    )

    # Clips that transcribe to nothing are saved here for diagnosis; empty disables.
    debug_clips_dir: str = field(default_factory=lambda: _env("DEBUG_CLIPS_DIR", str(SERVER_ROOT / "debug_clips")))
    debug_clips_max: int = 50

    sample_rate: int = 16000  # board <-> server PCM rate, mono int16 LE
    max_record_seconds: float = 20.0
    history_turns: int = 8
    session_idle_seconds: float = 600.0
    audio_ttl_seconds: float = 120.0
