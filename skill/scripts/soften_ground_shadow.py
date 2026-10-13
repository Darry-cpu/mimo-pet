#!/usr/bin/env python3
"""Replace the cyan ground shadow baked into the pet atlas with a soft neutral one.

The generated frames were drawn on a cyan background and the generator also painted a
cyan ground shadow under the feet. Chroma keying removes the background but keeps that
shadow, so the pet carries a blue blob under its shoes - invisible while standing still,
obvious while it runs, because the feet slide through it.

This finds the shadow region per cell and repaints it as a soft dark ellipse (alpha
scaled down and capped), which keeps the grounding without the colour cast. Anything
that is clearly the character (opaque and not blue-shifted) is left untouched, and the
region is only ever built from pixels below ``--band`` so the character cannot be hit.

Usage:
    python soften_ground_shadow.py <atlas.webp> [--out atlas_fixed.webp] [--remove]

    --remove   drop the shadow entirely instead of repainting it
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np
from PIL import Image

CELL_W, CELL_H = 192, 208


def _neighbours(y: int, x: int):
    yield y + 1, x
    yield y - 1, x
    yield y, x + 1
    yield y, x - 1


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(mask, dtype=bool)
    found: list[list[tuple[int, int]]] = []
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue = collections.deque([(y, x)])
            seen[y, x] = True
            pixels: list[tuple[int, int]] = []
            while queue:
                cy, cx = queue.popleft()
                pixels.append((cy, cx))
                for ny, nx in _neighbours(cy, cx):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            found.append(pixels)
    return found


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = np.zeros_like(mask)
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return out
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out[np.clip(ys + dy, 0, mask.shape[0] - 1),
                np.clip(xs + dx, 0, mask.shape[1] - 1)] = True
    return out


def shadow_mask(cell: np.ndarray, band: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (shadow pixels to repaint, stray chroma-key pixels)."""
    height = cell.shape[0]
    r, g, b, a = cell[..., 0], cell[..., 1], cell[..., 2], cell[..., 3]
    bg_max = np.maximum(r, g)

    blue = (a > 0) & (b - bg_max > 12)
    faint_dark = (a > 0) & (a <= 150) & (cell[..., :3].max(axis=2) <= 80)
    shadowish = blue | faint_dark
    shadowish[:band] = False                      # never look at the character's body

    seed = np.zeros_like(shadowish)
    for pixels in _components(shadowish):
        if min(p[0] for p in pixels) >= band - 6 and len(pixels) >= 4:
            for y, x in pixels:
                seed[y, x] = True

    region = _dilate(seed, 2)
    solid = np.zeros_like(region)                 # fill each row between its own extents
    span: tuple[int, int] | None = None
    for y in range(band, height):
        xs = np.flatnonzero(region[y])
        if len(xs):
            span = (int(xs.min()), int(xs.max()))
        if span:
            solid[y, span[0]:span[1] + 1] = True

    definite_char = (a >= 150) & ((b - bg_max) <= 6)
    rows = np.arange(height)[:, None] >= band
    shadow = (solid & (a > 0) & ~definite_char) | (blue & (a > 0) & rows)
    key = (a > 0) & (b > 200) & (r < 60) & (g < 60)
    return shadow, key


def soften(img: Image.Image, band: int, tint: tuple[int, int, int],
           alpha_scale: float, alpha_cap: int, remove: bool) -> tuple[Image.Image, dict]:
    arr = np.asarray(img.convert("RGBA")).astype(np.float64)
    src = np.asarray(img.convert("RGBA")).astype(np.int16)
    shadow_px = key_px = 0
    for row0 in range(0, arr.shape[0], CELL_H):
        for col0 in range(0, arr.shape[1], CELL_W):
            cell = src[row0:row0 + CELL_H, col0:col0 + CELL_W]
            if cell.shape[0] != CELL_H or cell.shape[1] != CELL_W:
                continue
            shadow, key = shadow_mask(cell, band)
            sub = arr[row0:row0 + CELL_H, col0:col0 + CELL_W]
            if remove:
                sub[shadow, 3] = 0
            else:
                sub[shadow, 0] = tint[0]
                sub[shadow, 1] = tint[1]
                sub[shadow, 2] = tint[2]
                sub[shadow, 3] = np.clip(cell[shadow][:, 3] * alpha_scale, 0, alpha_cap)
            sub[key, 3] = 0
            shadow_px += int(shadow.sum())
            key_px += int(key.sum())
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    return out, {"shadow_pixels": shadow_px, "chroma_key_pixels": key_px}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("atlas")
    ap.add_argument("--out", help="output path (default: overwrite the input)")
    ap.add_argument("--band", type=int, default=175, help="first row the shadow may live in")
    ap.add_argument("--tint", default="#24242A", help="replacement shadow colour")
    ap.add_argument("--alpha-scale", type=float, default=0.30)
    ap.add_argument("--alpha-cap", type=int, default=78)
    ap.add_argument("--remove", action="store_true", help="delete the shadow instead")
    args = ap.parse_args()

    tint = tuple(int(args.tint[i:i + 2], 16) for i in (1, 3, 5))
    src = Path(args.atlas).expanduser().resolve()
    dst = Path(args.out).expanduser().resolve() if args.out else src

    img = Image.open(src)
    fixed, stats = soften(img, args.band, tint, args.alpha_scale, args.alpha_cap, args.remove)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() == ".webp":
        fixed.save(dst, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    else:
        fixed.save(dst, format="PNG")

    print(json.dumps({"ok": True, "in": str(src), "out": str(dst),
                      "mode": "remove" if args.remove else "soften",
                      "size_bytes": dst.stat().st_size, **stats}, indent=2))


if __name__ == "__main__":
    main()
