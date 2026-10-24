"""Render 小奇 skins (tools/skins.js) into a binary frame atlas for the firmware.

Output data/skins.bin: SKIN_COUNT x FRAME_COUNT frames of FRAME_SIZE^2 RGB565,
big-endian (LovyanGFX swap565 order), with the UI background gradient baked in so
the firmware can blit frames opaquely. Also writes tools/preview.png for review.

usage: python3 tools/gen_skins.py
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger("gen_skins")

ROOT = Path(__file__).resolve().parent.parent
SKINS_JS = ROOT / "tools" / "skins.js"
OUT_BIN = ROOT / "data" / "skins.bin"
OUT_HEADER = ROOT / "include" / "skin_frames.h"
PREVIEW = ROOT / "tools" / "preview.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FRAME_SIZE = 120
PIXEL_GRID = 40  # pixel skin: render at 40 px, upscale x3 to 120
SKIN_ORDER = ["A", "B", "C"]  # B (pixel) is derived from A

# Must match firmware: frame top-left on screen and buildBackground() gradient.
FRAME_X, FRAME_Y = 100, 14
BG_CX, BG_CY, BG_RADIUS = 160, 78 + 22, 210.0
C_BG_CENTER, C_BG_EDGE = 0x1845, 0x0001


def frame_names() -> list[str]:
    src = SKINS_JS.read_text()
    body = src[src.index("const FRAMES = {") : src.index("};", src.index("const FRAMES = {"))]
    return re.findall(r"^\s{2}(\w+):", body, re.MULTILINE)


def blend565(a: int, b: int, t: float) -> int:
    """Same integer math as the firmware's blend()."""
    t = min(max(t, 0.0), 1.0)
    ar, ag, ab = a >> 11, (a >> 5) & 63, a & 31
    br, bg, bb = b >> 11, (b >> 5) & 63, b & 31
    return (int(ar + (br - ar) * t) << 11) | (int(ag + (bg - ag) * t) << 5) | int(ab + (bb - ab) * t)


def to888(c: np.ndarray) -> np.ndarray:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1).astype(np.uint8)


def to565(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.uint16) for i in range(3))
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def background_patch() -> np.ndarray:
    ys, xs = np.mgrid[FRAME_Y : FRAME_Y + FRAME_SIZE, FRAME_X : FRAME_X + FRAME_SIZE]
    d = np.sqrt((xs - BG_CX) ** 2 + (ys - BG_CY) ** 2) / BG_RADIUS
    patch = np.vectorize(lambda t: blend565(C_BG_CENTER, C_BG_EDGE, float(t)), otypes=[np.uint16])(d)
    return to888(patch)


# Pixel skin palette: skin A's flat colours plus the effect colours.
PIXEL_PALETTE = np.array([
    (0x5b, 0x65, 0x76), (0x3d, 0x46, 0x56), (0xfb, 0xfb, 0xfd), (0x4f, 0xa3, 0xff), (0x14, 0x21, 0x3d),
    (0xf6, 0xb8, 0xc8), (0xff, 0x9f, 0xb6), (0x7a, 0x2e, 0x3b), (0xff, 0x7b, 0x9c), (0x1d, 0x24, 0x33),
    (0xff, 0xd6, 0x0a), (0x05, 0xff, 0xa1), (0x7c, 0xc4, 0xff), (0xcf, 0xe8, 0xff),
], dtype=np.int32)


def render_grid(size: int, skins: list[str], n_frames: int, workdir: Path, crisp: bool = False) -> Image.Image:
    html = workdir / "grid.html"
    html.write_text(f"<!doctype html><meta charset=utf-8><script src='{SKINS_JS.as_uri()}'></script>"
                    "<body onload='renderGrid()'></body>")
    shot = workdir / f"grid_{size}.png"
    w, h = size * n_frames, size * len(skins)
    url = f"{html.as_uri()}?size={size}&skins={','.join(skins)}&crisp={int(crisp)}"
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
           "--allow-file-access-from-files", "--default-background-color=00000000",
           f"--window-size={w},{h}", f"--screenshot={shot}", url]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    img = Image.open(shot).convert("RGBA")
    if img.size != (w, h):
        raise RuntimeError(f"unexpected screenshot size {img.size}, wanted {(w, h)}")
    return img


def composite(rgba: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return (rgba[..., :3] * a + bg * (1 - a) + 0.5).astype(np.uint8)


def pixelize(small: Image.Image) -> np.ndarray:
    """Hard-edged sprite snapped to PIXEL_PALETTE with a dark outline, upscaled to FRAME_SIZE."""
    arr = np.array(small)
    solid = arr[..., 3] >= 128
    d = ((arr[..., None, :3].astype(np.int32) - PIXEL_PALETTE[None, None]) ** 2).sum(-1)
    rgb = PIXEL_PALETTE[d.argmin(-1)].astype(np.uint8)
    outline = np.zeros_like(solid)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        outline |= np.roll(np.roll(solid, dy, 0), dx, 1)
    outline &= ~solid
    out = np.zeros((*solid.shape, 4), np.uint8)
    out[solid, :3], out[solid, 3] = rgb[solid], 255
    out[outline, :3], out[outline, 3] = (20, 24, 36), 255
    scale = FRAME_SIZE // PIXEL_GRID
    return np.kron(out, np.ones((scale, scale, 1), np.uint8))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    names = frame_names()
    n = len(names)
    bg = background_patch()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        big = np.array(render_grid(FRAME_SIZE, ["A", "C"], n, work))
        small = render_grid(PIXEL_GRID, ["A"], n, work, crisp=True)

    frames: dict[str, list[np.ndarray]] = {s: [] for s in SKIN_ORDER}
    for i in range(n):
        cols = slice(i * FRAME_SIZE, (i + 1) * FRAME_SIZE)
        frames["A"].append(composite(big[:FRAME_SIZE, cols], bg))
        frames["C"].append(composite(big[FRAME_SIZE:, cols], bg))
        cell = small.crop((i * PIXEL_GRID, 0, (i + 1) * PIXEL_GRID, PIXEL_GRID))
        frames["B"].append(composite(pixelize(cell), bg))

    OUT_BIN.parent.mkdir(exist_ok=True)
    with OUT_BIN.open("wb") as f:
        for s in SKIN_ORDER:
            for fr in frames[s]:
                f.write(to565(fr).astype(">u2").tobytes())

    enum = ",\n".join(f"  {name.upper()} = {i}" for i, name in enumerate(names))
    OUT_HEADER.write_text(
        "#pragma once\n// Generated by tools/gen_skins.py — do not edit.\n#include <stdint.h>\n\n"
        f"constexpr int SKIN_FRAME_SIZE = {FRAME_SIZE};\nconstexpr int SKIN_FRAME_X = {FRAME_X};\n"
        f"constexpr int SKIN_FRAME_Y = {FRAME_Y};\nconstexpr int SKIN_ART_COUNT = {len(SKIN_ORDER)};\n"
        f"constexpr int SKIN_FRAME_COUNT = {n};\n\nenum class Frame : uint8_t {{\n{enum}\n}};\n"
    )

    sheet = np.concatenate([np.concatenate(frames[s], axis=1) for s in SKIN_ORDER], axis=0)
    Image.fromarray(sheet).save(PREVIEW)
    log.info("wrote %s (%d bytes), %s, %s", OUT_BIN, OUT_BIN.stat().st_size, OUT_HEADER.name, PREVIEW.name)
    log.info("frames: %s", json.dumps(names))


if __name__ == "__main__":
    main()
