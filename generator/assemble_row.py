#!/usr/bin/env python3
"""Assemble generated poses into atlas cells that match the existing rows' metrics.

Poses come back from the image model at an arbitrary scale on an off-cyan background, so
they cannot simply be dropped into the atlas: the character would be a different size than
the eight rows already in it (which are all exactly 198px tall with the soles at y=202).

This keys each pose against the background colour it actually used, trims the cyan fringe,
scales every frame by ONE shared factor (so the character keeps her size), and places each
frame horizontally centred with its soles at the height you ask for - which is also how the
jump arc is defined (grounded frames at the ground line, airborne frames above it).

usage:
    python assemble_row.py --out row.png \
        --pose crouch.png --pose launch.png --pose apex.png \
        --feet 202 --feet 202 --feet 150
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CELL_W, CELL_H = 192, 208
GROUND_Y = 202                 # where the soles sit in the rows already in the atlas
BODY_H = 198                   # how tall the character is in those rows
CENTER_X = CELL_W // 2
ALPHA_MIN = 32


def _defringe_module(skill_dir: Path):
    spec = importlib.util.spec_from_file_location("defringe", skill_dir / "defringe_atlas.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def background_colour(arr: np.ndarray, border: int = 6) -> np.ndarray:
    """The flat colour the generator actually used, sampled from the image border."""
    edges = np.concatenate([arr[:border].reshape(-1, 3), arr[-border:].reshape(-1, 3),
                            arr[:, :border].reshape(-1, 3), arr[:, -border:].reshape(-1, 3)])
    return np.median(edges, axis=0)


def key(img: Image.Image, fill_tol: float = 60.0, lo: float = 45.0, hi: float = 115.0) -> tuple[Image.Image, int]:
    """Derive alpha for a pose drawn on the flat cyan backdrop.

    The generated cyan is rarely uniform (it comes back with a soft vignette), so a plain
    distance threshold leaves a veil over the background. The flat area is found by flood
    filling from the corners instead, and only the thin band around the character keeps a
    graded alpha.
    """
    arr = np.asarray(img.convert("RGB")).astype(np.float64)
    bg = background_colour(arr)
    dist = np.linalg.norm(arr - bg[None, None, :], axis=-1)

    probe = Image.fromarray(np.clip(255.0 - dist, 0, 255).astype(np.uint8), "L").convert("RGB")
    marker = (255, 0, 255)
    for seed in ((0, 0), (probe.width - 1, 0), (0, probe.height - 1),
                 (probe.width - 1, probe.height - 1)):
        ImageDraw.floodfill(probe, seed, marker, thresh=255.0 - fill_tol)
    marked = np.asarray(probe)
    is_background = (marked[..., 0] == marker[0]) & (marked[..., 1] == marker[1]) & (marked[..., 2] == marker[2])

    alpha = np.clip((dist - lo) / (hi - lo), 0.0, 1.0)
    alpha[is_background] = 0.0
    af = np.clip(alpha, 1e-6, 1.0)[..., None]
    fg = np.clip((arr - (1.0 - af) * bg[None, None, :]) / af, 0, 255)
    still_key = ~is_background & (alpha > 0) & (np.linalg.norm(fg - bg[None, None, :], axis=-1) < 30.0)
    alpha = np.where(still_key, 0.0, alpha)
    fg = np.where((alpha <= 0)[..., None], 0.0, fg)
    return Image.fromarray(np.dstack([fg, alpha[..., None] * 255.0]).astype(np.uint8), "RGBA"), int(still_key.sum())


def body_box(img: Image.Image) -> tuple[int, int, int, int]:
    arr = np.asarray(img)
    mask = arr[..., 3] >= ALPHA_MIN
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise SystemExit("a pose came back empty")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def assemble(poses: list[Image.Image], feet: list[int], skill_dir: Path,
             scale_hint: float | None = None) -> tuple[Image.Image, list[dict]]:
    defringe = _defringe_module(skill_dir)
    keyed, kills = [], 0
    for p in poses:
        clean, killed = key(p)
        kills += killed
        keyed.append(clean)
    boxes = [body_box(k) for k in keyed]
    heights = [b[3] - b[1] for b in boxes]
    # one shared scale for the whole row: bigger frames are airborne, so anchor the scale on
    # the tallest grounded pose instead of on every frame individually
    grounded = [h for h, y in zip(heights, feet) if y == GROUND_Y] or heights
    # the anchor is the TALLEST grounded pose: a crouch is legitimately shorter than the
    # character, so anchoring on it would blow the standing frames up past the cell
    base_h = scale_hint or float(max(grounded))
    scale = BODY_H / base_h

    cells, report = [], []
    for clean, box, target_y, h in zip(keyed, boxes, feet, heights):
        w, hh = clean.size
        big = clean.resize((max(1, round(w * scale)), max(1, round(hh * scale))), Image.LANCZOS)
        b = [round(v * scale) for v in box]
        cx = (b[0] + b[2]) / 2
        cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
        cell.alpha_composite(big, (round(CENTER_X - cx), round(target_y - b[3])))
        arr = np.asarray(cell)
        mask = arr[..., 3] >= ALPHA_MIN
        ys, xs = np.nonzero(mask)
        report.append({"height": int(ys.max() - ys.min() + 1), "top": int(ys.min()),
                       "bottom": int(ys.max()), "width": int(xs.max() - xs.min() + 1),
                       "area": int(mask.sum())})
        cells.append(cell)

    strip = Image.new("RGBA", (CELL_W * len(cells), CELL_H))
    for i, c in enumerate(cells):
        strip.paste(c, (i * CELL_W, 0))
    for r in report:
        r["fringe_killed"] = kills
    return strip, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pose", action="append", required=True, help="pose image, in frame order")
    ap.add_argument("--feet", action="append", type=int, required=True,
                    help=f"soles y for each pose ({GROUND_Y} = standing on the ground line)")
    ap.add_argument("--out", required=True, help="where to write the assembled row strip")
    ap.add_argument("--skill-dir", default="skill/scripts")
    ap.add_argument("--scale-hint", type=float, default=None,
                    help="override the body height the scale is anchored on")
    a = ap.parse_args()

    if len(a.pose) != len(a.feet):
        raise SystemExit("--pose and --feet must be given the same number of times")
    poses = [Image.open(p).convert("RGBA") for p in a.pose]
    skill_dir = Path(a.skill_dir).expanduser().resolve()
    strip, report = assemble(poses, a.feet, skill_dir, a.scale_hint)
    out = Path(a.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    strip.save(out)
    ref = {"height": BODY_H, "top": 5, "bottom": GROUND_Y, "width": "119-165", "area": "14600-19900"}
    print(json.dumps({"ok": True, "out": str(out), "frames": len(poses),
                      "reference_row": ref, "frames_metrics": report}, indent=2))


if __name__ == "__main__":
    main()