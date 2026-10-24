"""Side-by-side listening test of local Chinese TTS engines for 小奇.

Writes samples/<engine>_<n>.wav and samples/index.html (open it in a browser to compare).
usage: .venv/bin/python eval_tts.py [engine ...]
"""
from __future__ import annotations

import html
import json
import logging
import sys
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("eval_tts")
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "samples"
PIPER_VOICE = ROOT.parent / "server" / "models" / "zh_CN-huayan-medium.onnx"

# Typical replies: excitement, numbers/units, a percentage, English terms, comfort.
SENTENCES = [
    "哇，太棒了，恭喜你升职！今晚必须好好庆祝一下～",
    "明天上海多云转小雨，气温十七到二十二度，出门记得带伞哦。",
    "我帮你查了一下，英伟达昨天收盘下跌了百分之二点八。",
    "好的，我已经帮你打开 GitHub，最新的 pull request 有三个。",
    "别担心，多喝热水，好好休息，很快就会好起来的。",
]

# engine id -> (label, kind, repo/model, kwargs)
ENGINES = {
    "piper": ("Piper huayan（现在用的）", "piper", str(PIPER_VOICE), {}),
    "kokoro": ("Kokoro 82M · zf_xiaoxiao", "mlx", "mlx-community/Kokoro-82M-bf16", {"voice": "zf_xiaoxiao", "lang_code": "z"}),
    "qwen06": ("Qwen3-TTS 0.6B", "mlx", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-bf16", {"lang_code": "chinese"}),
    "qwen17": ("Qwen3-TTS 1.7B (6bit)", "mlx", "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit", {"lang_code": "chinese"}),
    "voxcpm2": ("VoxCPM2 (4bit)", "mlx", "mlx-community/VoxCPM2-4bit", {}),
}
# Preferred Qwen3-TTS preset speakers for a friendly Mandarin voice, first match wins.
QWEN_SPEAKER_PREFS = ["vivian", "serena", "uncle_fu", "dylan", "eric"]


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def run_piper(model: str) -> list[tuple[np.ndarray, int, float]]:
    from piper import PiperVoice

    voice = PiperVoice.load(model)
    out = []
    for text in SENTENCES:
        t = time.perf_counter()
        chunks = list(voice.synthesize(text))
        audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, "<i2") for c in chunks]).astype(np.float32) / 32768
        out.append((audio, chunks[0].sample_rate, time.perf_counter() - t))
    return out


def run_mlx(repo: str, kwargs: dict, engine: str) -> tuple[list[tuple[np.ndarray, int, float]], str]:
    from mlx_audio.tts.utils import load_model

    model = load_model(repo)
    note = ""
    if engine.startswith("qwen"):
        spk = getattr(getattr(model.config, "talker_config", None), "spk_id", None) or {}
        names = list(spk)
        pick = next((s for s in QWEN_SPEAKER_PREFS if s in names), names[0] if names else None)
        if pick:
            kwargs = {**kwargs, "voice": pick}
        note = f"speaker={pick} (available: {', '.join(names)})"
    out = []
    # First call compiles kernels; exclude it from timing.
    list(model.generate(text="你好", **kwargs))
    for text in SENTENCES:
        t = time.perf_counter()
        results = list(model.generate(text=text, **kwargs))
        audio = np.concatenate([np.array(r.audio, dtype=np.float32).reshape(-1) for r in results])
        out.append((audio, results[0].sample_rate, time.perf_counter() - t))
    return out, note


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUT.mkdir(exist_ok=True)
    wanted = sys.argv[1:] or list(ENGINES)
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    for eid in wanted:
        label, kind, model, kwargs = ENGINES[eid]
        log.info("== %s", label)
        try:
            if kind == "piper":
                res, note = run_piper(model), ""
            else:
                res, note = run_mlx(model, dict(kwargs), eid)
        except Exception as e:  # keep evaluating the other engines
            log.error("   %s failed: %s", eid, e)
            meta[eid] = {"label": label, "error": str(e)[:300]}
            continue
        rows = []
        for i, (audio, rate, secs) in enumerate(res, 1):
            write_wav(OUT / f"{eid}_{i}.wav", audio, rate)
            dur = audio.size / rate
            rows.append({"secs": round(secs, 2), "dur": round(dur, 2), "rtf": round(secs / max(dur, 1e-6), 2)})
            log.info("   %d: synth %.2fs for %.1fs audio (rtf %.2f)", i, secs, dur, secs / max(dur, 1e-6))
        meta[eid] = {"label": label, "note": note, "rows": rows}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    write_index(meta)


def write_index(meta: dict) -> None:
    head = "".join(f"<th>{i}. {html.escape(s)}</th>" for i, s in enumerate(SENTENCES, 1))
    body = []
    for eid, m in meta.items():
        if "error" in m:
            body.append(f"<tr><td><b>{html.escape(m['label'])}</b></td><td colspan=5>失败：{html.escape(m['error'])}</td></tr>")
            continue
        cells = "".join(
            f"<td><audio controls preload=none src='{eid}_{i}.wav'></audio><br><small>合成 {r['secs']}s · 时长 {r['dur']}s</small></td>"
            for i, r in enumerate(m["rows"], 1))
        avg = sum(r["secs"] for r in m["rows"]) / len(m["rows"])
        body.append(f"<tr><td><b>{html.escape(m['label'])}</b><br><small>平均合成 {avg:.2f}s<br>{html.escape(m.get('note', ''))}</small></td>{cells}</tr>")
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>小奇 TTS 对比</title>"
        "<style>body{font-family:-apple-system,sans-serif;margin:20px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:6px;vertical-align:top;font-size:13px}th{max-width:180px}audio{width:170px}</style>"
        "<h2>小奇 TTS 对比</h2><p>同样 5 句话，每行一个引擎。合成时间越短，小奇开口越快。</p>"
        f"<table><tr><th>引擎</th>{head}</tr>{''.join(body)}</table>")


if __name__ == "__main__":
    main()
