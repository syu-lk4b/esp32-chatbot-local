"""PCM helpers. Board audio is 16 kHz mono int16 little-endian."""
from __future__ import annotations

import numpy as np

# Below this RMS (int16 scale) a clip is treated as silence. Board noise floor is ~4-6 and
# normal speech averages ~70 over a clip (pre-roll and trailing silence included); Whisper
# still transcribes correctly at 15, while the old gate of 60 dropped quieter speech.
SILENCE_RMS = 15.0


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(audio: np.ndarray) -> bytes:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def rms_int16(pcm: bytes) -> float:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples**2)))


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampler; adequate for speech going to a small speaker."""
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32)
    duration = audio.size / src_rate
    n_out = max(1, round(duration * dst_rate))
    src_t = np.arange(audio.size) / src_rate
    dst_t = np.arange(n_out) / dst_rate
    return np.interp(dst_t, src_t, audio).astype(np.float32)


def normalize(audio: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(audio))) if audio.size else 0.0
    if m < 1e-6:
        return audio
    return audio * (peak / m)
