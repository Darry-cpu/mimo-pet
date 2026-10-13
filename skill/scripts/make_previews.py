#!/usr/bin/env python3
"""Rebuild previews/ from the atlas: one animated GIF per state plus the contact sheet.

Run it after touching assets/spritesheet.webp so the README previews keep matching the
art (the shadows and colours in here are just the atlas, downscaled half size).

    python make_previews.py --atlas ../../assets/spritesheet.webp --outdir ../../previews

Frame timings for the GIFs are taken from the GIFs already on disk, so re-running only
replaces the pixels; pass --reset-timing to fall back to the durations in this file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CELL_W, CELL_H = 192, 208

# mirrors app/mimo_pet.py: name -> (row, frame count, durations in ms)
STATES: dict[str, tuple[int, int, list[int]]] = {
    "idle":          (0, 6, [1680, 660, 660, 840, 840, 1920]),
    "running-right": (1, 8, [120] * 7 + [220]),
    "running-left":  (2, 8, [120] * 7 + [220]),
    "waving":        (3, 4, [140, 140, 140, 280]),
    "jumping":       (4, 5, [140, 140, 140, 140, 280]),
    "failed":        (5, 8, [140] * 7 + [240]),
    "waiting":       (6, 6, [150] * 5 + [260]),
    "running":       (7, 6, [120] * 5 + [220]),
    "review":        (8, 6, [150] * 5 + [280]),
}

GIF_STATES = ["idle", "running-right", "running-left", "jumping", "waiting", "review"]

SCALE = 0.5                      # the contact sheet shows each cell half size
GRID_COLS = 8
ROW_BAND = 22                    # dark strip that carries the row label
CELL_SMALL = (int(CELL_W * SCALE), int(CELL_H * SCALE))
CHECKER = 16
CHECKER_A, CHECKER_B = (232, 232, 232), (255, 255, 255)
BAND_BG = (17, 17, 17)
GREEN = (24, 160, 88)
RED = (204, 51, 68)
INK = (17, 17, 17)

FONT_CANDIDATES = [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def cell(sheet: Image.Image, row: int, col: int) -> Image.Image:
    return sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))


def checkerboard(size: tuple[int, int], origin: tuple[int, int]) -> Image.Image:
    """Flat checkerboard; ``origin`` keeps the pattern aligned across cells."""
    w, h = size
    tile = np.zeros((h, w, 3), np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    phase = ((xx + origin[0]) // CHECKER + (yy + origin[1]) // CHECKER) % 2
    tile[phase == 0] = CHECKER_A
    tile[phase == 1] = CHECKER_B
    return Image.fromarray(tile, "RGB")


def build_gif(sheet: Image.Image, name: str, row: int, count: int,
              durations: list[int], outdir: Path) -> Path:
    frames = [cell(sheet, row, c) for c in range(count)]
    frames = frames + [frames[-1]] * (len(durations) - len(frames)) if len(durations) > len(frames) else frames

    # one shared palette for every frame, index 0 reserved for transparency
    flat = Image.new("RGB", (CELL_W * len(frames), CELL_H))
    for i, f in enumerate(frames):
        flat.paste(f.convert("RGB"), (i * CELL_W, 0))
    quantised = flat.quantize(colors=255, method=Image.MEDIANCUT, dither=Image.NONE)

    palette = quantised.getpalette()[:255 * 3]
    palette = [0, 0, 0] + palette
    palette += [0] * (768 - len(palette))

    out_frames = []
    for i, f in enumerate(frames):
        idx = np.asarray(quantised.crop((i * CELL_W, 0, (i + 1) * CELL_W, CELL_H))).astype(np.int16) + 1
        idx[np.asarray(f)[..., 3] < 128] = 0
        frame = Image.fromarray(idx.astype(np.uint8), "P")
        frame.putpalette(palette)
        out_frames.append(frame)

    path = outdir / f"{name}.gif"
    out_frames[0].save(path, save_all=True, append_images=out_frames[1:],
                       duration=durations, loop=0, transparency=0, disposal=2, optimize=True)
    return path


def build_contact_sheet(sheet: Image.Image, outdir: Path) -> Path:
    rows = len(STATES)
    width = GRID_COLS * CELL_SMALL[0]
    height = rows * (ROW_BAND + CELL_SMALL[1])
    canvas = Image.new("RGB", (width, height), BAND_BG)
    draw = ImageDraw.Draw(canvas)
    label_font, index_font = font(13), font(11)

    for r, (name, (row, count, _durs)) in enumerate(STATES.items()):
        top = r * (ROW_BAND + CELL_SMALL[1])
        draw.text((6, top + 4), f"row {row} · {name}", font=label_font, fill=(255, 255, 255))
        frames_text = f"{count} frames"
        tw = draw.textlength(frames_text, font=label_font)
        draw.text((width - tw - 6, top + 4), frames_text, font=label_font, fill=(255, 255, 255))

        grid_top = top + ROW_BAND
        for c in range(GRID_COLS):
            x0 = c * CELL_SMALL[0]
            used = c < count
            tile = checkerboard(CELL_SMALL, (x0, grid_top))
            if used:
                sprite = cell(sheet, row, c).resize(CELL_SMALL, Image.LANCZOS)
                tile = tile.convert("RGBA")
                tile.alpha_composite(sprite)
                tile = tile.convert("RGB")
            canvas.paste(tile, (x0, grid_top))
            draw.rectangle([x0, grid_top, x0 + CELL_SMALL[0] - 1, grid_top + CELL_SMALL[1] - 1],
                           outline=GREEN if used else RED)
            if used:
                draw.text((x0 + 4, grid_top + 3), str(c), font=index_font, fill=INK)

    path = outdir / "contact-sheet.png"
    canvas.save(path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--reset-timing", action="store_true",
                    help="use the durations in this file instead of the ones on disk")
    args = ap.parse_args()

    atlas = Path(args.atlas).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(atlas).convert("RGBA")

    written = {"gifs": [], "contact_sheet": None, "timing": {}}
    for name in GIF_STATES:
        row, count, fallback = STATES[name]
        existing = outdir / f"{name}.gif"
        durations = fallback
        if existing.is_file() and not args.reset_timing:
            probe = Image.open(existing)
            durations, i = [], 0
            try:
                while True:
                    probe.seek(i)
                    durations.append(probe.info.get("duration") or fallback[0])
                    i += 1
            except EOFError:
                pass
            if len(durations) != count:
                durations = fallback
        written["timing"][name] = durations
        written["gifs"].append(build_gif(sheet, name, row, count, durations, outdir).name)

    written["contact_sheet"] = build_contact_sheet(sheet, outdir).name
    print(json.dumps({"ok": True, "atlas": str(atlas), "outdir": str(outdir), **written}, indent=2))


if __name__ == "__main__":
    main()
