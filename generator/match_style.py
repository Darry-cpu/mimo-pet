#!/usr/bin/env python3
"""Bring generated frames back onto the atlas's art style (line weight, tone, colour).

Model output comes back as soft, gradient-shaded art with almost no line work: measured
against the rows already in the atlas it carries ~1% dark pixels where they carry ~20%, and
its average brightness is ~40 higher. Dropped straight into the atlas it reads as washed
out next to the rest of the animations, even when the pose and the size are right.

This restores the missing parts numerically, so it can be tuned against the atlas instead of
hoping the next generation behaves:

  * a dark outline is drawn along the silhouette (outer stroke plus a thin inner band),
  * a gentle S-curve puts contrast back into the flats,
  * saturation is nudged towards the reference row.

usage:
    python match_style.py --row row.png --out row-styled.png --reference ../../assets/spritesheet.webp
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CELL_W, CELL_H = 192, 208
OPAQUE = 200            # pixels this opaque count as "the drawing" when measuring tone


def tone_stats(frames: np.ndarray) -> dict:
    a = frames[..., 3] >= OPAQUE
    rgb = frames[..., :3][a].astype(np.float64)
    if not len(rgb):
        return {}
    L = rgb.mean(1)
    mx, mn = rgb.max(1), rgb.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    return {"dark_pct": float((L < 80).mean() * 100),
            "luma": float(L.mean()),
            "contrast": float(np.percentile(L, 95) - np.percentile(L, 5)),
            "sat": float(sat.mean())}


def reference_frames(atlas_path: Path, rows: list[int], cols: int = 6) -> np.ndarray:
    sheet = np.asarray(Image.open(atlas_path).convert("RGBA"))
    parts = [sheet[r * CELL_H:(r + 1) * CELL_H, :cols * CELL_W] for r in rows]
    return np.concatenate(parts, axis=1)


def outline_colour(frames: np.ndarray) -> np.ndarray:
    """The colour the atlas uses for its line work."""
    a = frames[..., 3] >= OPAQUE
    rgb = frames[..., :3][a].astype(np.float64)
    L = rgb.mean(1)
    dark = rgb[L < np.percentile(L, 12)]
    return np.median(dark, axis=0)


def trim_blue_cast(cell: np.ndarray, excess: float = 3.0, luma_min: float = 110.0,
                   sat_max: float = 0.35) -> np.ndarray:
    """Cut the blue (lavender) excess out of the light areas, without touching warm pixels.

    Only pixels whose blue channel actually exceeds their red are pulled down, so the hair
    loses its purple cast while the skin - which is warm, blue below red - cannot be affected.
    That is the difference from a blanket desaturation, which also drains the faces.
    """
    out = cell.astype(np.float32).copy()
    rgb, alpha = out[..., :3], out[..., 3]
    L = rgb.mean(2)
    sat = (rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1)
    mask = (alpha >= 200) & (L >= luma_min) & (sat < sat_max) & ((rgb[..., 2] - rgb[..., 0]) > excess)
    if not mask.any():
        return np.clip(out, 0, 255).astype(np.uint8)
    rgb[mask, 2] = rgb[mask, 0] + excess
    rgb[mask, 1] = np.minimum(rgb[mask, 1], rgb[mask, 2])
    return np.clip(out, 0, 255).astype(np.uint8)


def neutralise_greys(cell: np.ndarray, strength: float = 0.7, luma_min: float = 120.0,
                     luma_max: float = 250.0, sat_max: float = 0.30) -> np.ndarray:
    """Strip the hue out of the hair and every other near-white area.

    The hair comes back with a blue-lavender cast in its shading, which reads as purple next
    to the older rows. Rather than chasing the tint with channel gains, this pulls the light,
    low-saturation pixels towards their own luminance: the hair becomes neutral white/grey
    and the saturated colours (the orange trim, the black skirt) are untouched.
    """
    out = cell.astype(np.float32).copy()
    rgb, alpha = out[..., :3], out[..., 3]
    L = rgb.mean(2)
    sat = (rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1)
    # only cool, near-white pixels: the skin is light but warm (red well above blue), and
    # desaturating it turns every face grey, which is exactly what happened once already
    warm = (rgb[..., 0] - rgb[..., 2]) > 12.0
    mask = (alpha >= 200) & (L >= luma_min) & (L <= luma_max) & (sat < sat_max) & ~warm
    if not mask.any():
        return np.clip(out, 0, 255).astype(np.uint8)
    lum = L[mask][:, None]
    rgb[mask] = rgb[mask] * (1.0 - strength) + lum * strength
    return np.clip(out, 0, 255).astype(np.uint8)


def match_white_balance(cell: np.ndarray, target: np.ndarray, lo: float = 130.0,
                       hi: float = 235.0, sat_max: float = 0.25,
                       gain_range: tuple[float, float] = (0.85, 1.15)) -> np.ndarray:
    """Shift the whole frame so its greys sit where the atlas's greys sit.

    Correcting only the brightest pixels is not enough: the hair's shaded mid-tones are the
    big purple-looking area, and they come back noticeably blue-heavy (blue is 7-11 above
    red where the atlas rows sit between -2 and +8). A single per-channel gain derived from
    the grey pixels moves the whole frame - flats, shading and line work - onto the same
    white point, which keeps the palette consistent instead of introducing a second tint.
    """
    out = cell.astype(np.float32).copy()
    rgb, alpha = out[..., :3], out[..., 3]
    L = rgb.mean(2)
    sat = (rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1)
    grey = (alpha >= 200) & (L >= lo) & (L <= hi) & (sat < sat_max)
    if grey.sum() < 200:
        return np.clip(out, 0, 255).astype(np.uint8)
    have = rgb[grey].mean(0)
    gain = np.clip(target / np.maximum(have, 1.0), gain_range[0], gain_range[1])
    out[..., :3] = rgb * gain[None, None, :]
    return np.clip(out, 0, 255).astype(np.uint8)


def neutralize_whites(cell: np.ndarray, blue_offset: float = 3.5,
                      green_offset: float = -1.0, luma_min: float = 195.0,
                      sat_max: float = 0.12) -> np.ndarray:
    """Pull the tint of the white areas (hair, jacket, shoes) onto the atlas's white balance.

    The reference rows are very slightly cool - their white areas sit about +3.5 blue and
    -1 green relative to red. Generated frames drift a few units past that towards magenta
    (green sits 4-5 below red in places), which reads as purple hair next to the rest of
    the atlas. Only bright, low-saturation pixels are touched, so the orange and the black
    skirt are left alone.
    """
    out = cell.astype(np.float32).copy()
    rgb = out[..., :3]
    alpha = out[..., 3]
    L = rgb.mean(2)
    sat = (rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1)
    mask = (alpha >= 200) & (L > luma_min) & (sat < sat_max)
    if not mask.any():
        return np.clip(out, 0, 255).astype(np.uint8)
    mean = L[mask]
    rgb[mask, 0] = mean - (blue_offset + green_offset) / 3.0
    rgb[mask, 1] = mean + (2.0 * blue_offset - green_offset) / 3.0 - blue_offset + green_offset
    rgb[mask, 2] = mean + (2.0 * blue_offset - green_offset) / 3.0
    return np.clip(out, 0, 255).astype(np.uint8)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.astype(np.float32).copy()
    for _ in range(radius):
        pad = np.pad(out, 1, mode="edge")
        out = np.maximum.reduce([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2],
                                 pad[1:-1, 2:], pad[1:-1, 1:-1]])
    return out


def thicken_lines(cell: np.ndarray, ink: np.ndarray, rounds: int = 1,
                  dark_below: float = 118.0) -> np.ndarray:
    """Grow the line work that is already there, instead of drawing a new outline.

    The art comes back with the right line weight at generation size, but halving it to the
    192px atlas cell averages the thin dark outline away, so the frame reads flat next to
    the older rows. Dilating the existing dark pixels (and keeping them inside the figure)
    thickens the line by about a pixel per round without inflating the silhouette.
    """
    out = cell.astype(np.float32).copy()
    alpha = out[..., 3] / 255.0
    core = alpha > 0.15
    lum = out[..., :3].mean(2)
    line = core & (lum < dark_below)
    grown = line.astype(np.float32)
    for _ in range(rounds):
        pad = np.pad(grown, 1, mode="edge")
        grown = np.maximum.reduce([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2],
                                   pad[1:-1, 2:], pad[1:-1, 1:-1]])
    grown = np.clip(grown, 0, 1) * core
    out[..., :3] = out[..., :3] * (1 - grown[..., None]) + ink[None, None, :] * grown[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def style_frame(cell: np.ndarray, ink: np.ndarray, stroke: int, inner: float,
                contrast: float, sat_gain: float) -> np.ndarray:
    alpha = cell[..., 3].astype(np.float32) / 255.0
    rgb = cell[..., :3].astype(np.float32)

    # 1. tone: S-curve around mid grey, then pull saturation towards the reference
    x = rgb / 255.0
    x = np.clip(x + contrast * (x - 0.5) * (1.0 - np.abs(2.0 * x - 1.0)), 0, 1)
    lum = x.mean(2, keepdims=True)
    x = np.clip(lum + (x - lum) * sat_gain, 0, 1)
    rgb = x * 255.0

    # 2. line work: an outer stroke plus a darker inner band, sourced from the alpha so the
    #    stroke stays anti-aliased instead of turning into hard pixels
    grown = np.clip(dilate(alpha, stroke), 0, 1)
    outer = np.clip(grown - alpha, 0, 1)[..., None]

    # inner band: pixels of the drawing that sit close to the silhouette
    core = (alpha > 0.05).astype(np.float32)
    eroded = core.copy()
    for _ in range(int(round(inner * 3))):
        pad = np.pad(eroded, 1, mode="edge")
        eroded = np.minimum.reduce([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2],
                                    pad[1:-1, 2:], pad[1:-1, 1:-1]])
    inner_mask = np.clip(core - eroded, 0, 1)      # the outer ring of the drawing

    ink_rgb = ink.astype(np.float32)
    # paint the outer stroke
    rgb = rgb * (1.0 - outer) + ink_rgb[None, None, :] * outer
    out_alpha = np.maximum(alpha, grown * 0.96)
    # darken the thin inner band so the line reads at this size
    rgb = rgb * (1.0 - inner_mask[..., None] * 0.85) + ink_rgb[None, None, :] * (inner_mask[..., None] * 0.85)

    return np.dstack([np.clip(rgb, 0, 255), np.clip(out_alpha * 255.0, 0, 255)]).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--row", required=True, help="assembled row strip to restyle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--reference", required=True, help="atlas to take the style from")
    ap.add_argument("--rows", default="0,1,6", help="atlas rows to measure the style from")
    ap.add_argument("--stroke", type=int, default=2, help="outer stroke width in px")
    ap.add_argument("--inner", type=float, default=0.9, help="inner band strength (0-1)")
    ap.add_argument("--contrast", type=float, default=0.35)
    ap.add_argument("--sat", type=float, default=1.15, help="saturation gain")
    ap.add_argument("--report", default=None, help="write the measured stats to this json")
    ap.add_argument("--mode", choices=("stroke", "thicken", "whitebal", "greybal", "neutralgrey", "deblue"), default="stroke",
                    help="stroke: draw a new outer outline; thicken: grow the existing line work")
    ap.add_argument("--thicken", type=int, default=1, help="rounds of line thickening")
    a = ap.parse_args()

    ref = reference_frames(Path(a.reference).expanduser().resolve(),
                           [int(r) for r in a.rows.split(",")])
    ink = outline_colour(ref)
    refL = ref[..., :3].astype(np.float32).mean(2)
    refsat = ((ref[..., :3].astype(np.float32).max(2) - ref[..., :3].astype(np.float32).min(2))
              / np.maximum(ref[..., :3].astype(np.float32).max(2), 1))
    refgrey = (ref[..., 3] >= 200) & (refL >= 130) & (refL <= 235) & (refsat < 0.25)
    grey_target = ref[..., :3][refgrey].astype(np.float32).mean(0)
    row = np.asarray(Image.open(a.row).convert("RGBA"))
    styled = np.zeros_like(row)
    for x in range(0, row.shape[1], CELL_W):
        cell = row[:, x:x + CELL_W]
        if a.mode == "deblue":
            styled[:, x:x + CELL_W] = trim_blue_cast(cell)
        elif a.mode == "neutralgrey":
            styled[:, x:x + CELL_W] = neutralise_greys(cell)
        elif a.mode == "greybal":
            styled[:, x:x + CELL_W] = match_white_balance(cell, grey_target)
        elif a.mode == "whitebal":
            styled[:, x:x + CELL_W] = neutralize_whites(cell)
        elif a.mode == "thicken":
            styled[:, x:x + CELL_W] = thicken_lines(cell, ink, a.thicken)
        else:
            styled[:, x:x + CELL_W] = style_frame(cell, ink, a.stroke, a.inner,
                                                  a.contrast, a.sat)
    Image.fromarray(styled, "RGBA").save(Path(a.out).expanduser().resolve())

    report = {"ink": [float(v) for v in ink], "reference": tone_stats(ref),
              "styled": tone_stats(styled), "original": tone_stats(row)}
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()