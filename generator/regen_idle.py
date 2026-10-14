"""Regenerate the idle row as ONE 3x2 generation so all 6 frames stay consistent.

Why one image: the app plays idle as a single 6-frame loop (row 0, columns 0-5) with
fixed per-frame holds of 1680/660/660/840/840/1920 ms. Two separately generated
sub-strips left a visible seam, and inconsistent lighting/scaling read as stutter.
Generating all six poses in one shot keeps them internally consistent.

Layout: a 3x2 grid of equal slots, each slot with the same aspect ratio as the
192x208 cell, so no distortion when the frames are extracted.

Frame plan (matches the app's playback order and holds):
  0 calm stand                (1680 ms hold - rest)
  1 phone, looking at screen  ( 660 ms - static pose, reads fine on one frame)
  2 jog, left knee lifted     ( 660 ms - stride A)
  3 jog, right knee lifted    ( 840 ms - stride B; two frames make the jog read as motion)
  4 eating, spoon to mouth    ( 840 ms - static pose, reads fine on one frame)
  5 calm stand ~= frame 0     (1920 ms - rest; closes the loop smoothly)

Design note: the app's per-frame holds are fixed and cannot be changed, so the only
lever is frame CONTENT. Long holds (frames 0 and 5) are spent on the calm rest pose,
which keeps the loop looking calm instead of stuttering; the dynamic activity (jog)
gets two adjacent frames so it reads as motion rather than a frozen pose.
"""

from __future__ import annotations

import os
import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

RUN = Path(os.environ.get("MIMO_PET_RUN", "pet-run"))
SKILL = Path(os.environ.get("MIMO_SKILLS", "hatch-pet"))
CFG = Path(os.environ.get("MIMO_CONFIG", "mimocode.jsonc"))
MODEL = "qwen-image-2.0-pro-2026-06-22"
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

CHROMA_HEX = "#00FFFF"
CHROMA_RGB = (0, 255, 255)
SLOT_W, SLOT_H = 384, 416          # 2x the cell, same aspect as 192x208
COLS, ROWS = 3, 2
IMG_W, IMG_H = SLOT_W * COLS, SLOT_H * ROWS      # 1152 x 832


def api_key() -> str:
    t = CFG.read_text(encoding="utf-8")
    lines = [l for l in t.split("\n") if not l.strip().startswith("//")]
    clean = re.sub(r",(\s*[}\]])", r"\1", "\n".join(lines))
    return json.loads(clean)["provider"]["alibaba-cn"]["options"]["apiKey"]


def data_url(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def build_guide(out: Path) -> Path:
    """Layout guide: six equal slots in a 3x2 grid, faint outlines only."""
    img = Image.new("RGB", (IMG_W, IMG_H), CHROMA_RGB)
    d = ImageDraw.Draw(img)
    inset = 10
    for r in range(ROWS):
        for c in range(COLS):
            x0, y0 = c * SLOT_W + inset, r * SLOT_H + inset
            x1, y1 = (c + 1) * SLOT_W - inset, (r + 1) * SLOT_H - inset
            d.rectangle([x0, y0, x1, y1], outline=(120, 120, 120), width=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


PROMPT = """Using the second attached image as the EXACT character identity reference, draw ONE single image containing exactly SIX full-body frames of the SAME character, arranged in a 3x2 grid.

Grid order: top row left-to-right = frames 1, 2, 3. Bottom row left-to-right = frames 4, 5, 6.
The first attached image is a layout guide showing where the six slots are. Follow its slot positions but DO NOT draw the guide's boxes, lines, or background.

Every frame must show the SAME character, FRONT view, at the SAME body position, SAME scale, and SAME ground line. Only the arms, legs, head tilt and held props change. One complete full-body pose per slot, centered, not clipped, no overlapping between slots, no labels, no numbers, no borders.

Frame contents:
1. calm standing rest, both arms relaxed down at the sides, neutral happy face, feet together.
2. holding a small smartphone in both hands in front of the chest, head tilted down, looking at the screen.
3. jogging in place, LEFT knee lifted, arms bent and swinging, front view, still in the same spot.
4. jogging in place, RIGHT knee lifted, arms swung the other way, front view, same spot - this is the next stride of the same jog.
5. eating: holding a small bowl in one hand and a small spoon in the other, lifting the spoon up to the mouth, cheeks slightly puffed, happy face.
6. calm standing rest, identical to frame 1, so the loop ends exactly where it starts.

The props (phone, bowl, spoon) must be small, opaque, held in the hands, and must not float, detach, or overlap the slot edges.

Background: a perfectly flat uniform pure cyan #00FFFF filling the entire canvas, including behind the layout guide slots.
Absolutely no ground shadow, no contact shadow, no ground line, no ellipse or mark under the feet.
Hair must stay white/silver in EVERY frame; only the small top curl and the jacket/skirt/shoe accents are orange. Do not tint the hair orange, gold or yellow.
No scenery, no text, no shadows, no glow, no speed lines, no dust, no detached effects; keep cyan out of the character.
Style: clean cel-shaded chibi anime mascot, crisp outlines, flat bright colors, readable at small size. Keep the silhouette, face, proportions, palette, materials and props identical in every frame."""


def generate(prompt: str, images: list[Path], size: str, out: Path) -> Path:
    key = api_key()
    content = [{"image": data_url(p)} for p in images]
    content.append({"text": prompt})
    payload = {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"n": 1, "size": size},
    }
    for attempt in range(8):
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = json.loads(r.read().decode("utf-8"))
            img_url = body["output"]["choices"][0]["message"]["content"][0]["image"]
            with urllib.request.urlopen(img_url, timeout=120) as dr:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(dr.read())
            return out
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    rate limited, sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise SystemExit(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except Exception:
            if attempt >= 7:
                raise
            time.sleep(8)
    raise SystemExit("gave up after retries")


def main() -> None:
    run = RUN
    canonical = run / "references" / "canonical-base.png"
    if not canonical.is_file():
        raise SystemExit(f"missing canonical base: {canonical}")

    guide = build_guide(run / "references" / "layout-guides" / "idle-3x2.png")
    print(f"built guide {guide} ({IMG_W}x{IMG_H})")

    raw = run / "tmp" / "idle-grid-raw.png"
    print("generating 3x2 idle grid ...", flush=True)
    generate(PROMPT, [guide, canonical], f"{IMG_W}*{IMG_H}", raw)

    img = Image.open(raw).convert("RGB")
    print(f"raw returned: {img.size[0]}x{img.size[1]}")
    if img.size != (IMG_W, IMG_H):
        print(f"  resizing to {IMG_W}x{IMG_H}")
        img = img.resize((IMG_W, IMG_H), Image.Resampling.LANCZOS)

    # split into 6 frames in reading order and assemble a horizontal strip
    cells = []
    for r in range(ROWS):
        for c in range(COLS):
            cell = img.crop((c * SLOT_W, r * SLOT_H, (c + 1) * SLOT_W, (r + 1) * SLOT_H))
            cells.append(cell)

    strip = Image.new("RGB", (SLOT_W * 6, SLOT_H), CHROMA_RGB)
    for i, cell in enumerate(cells):
        strip.paste(cell, (i * SLOT_W, 0))
    strip_path = run / "tmp" / "idle-strip.png"
    strip.save(strip_path)
    print(f"assembled strip {strip.size}")

    # save individual cells for inspection
    for i, cell in enumerate(cells):
        cell.save(run / "tmp" / f"idle-cell-{i}.png")

    import subprocess, sys
    subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "mark_job_complete.py"),
         "--run-dir", str(run), "--job-id", "idle", "--source", str(strip_path)],
        check=True,
    )
    print("idle strip staged ->", run / "decoded" / "idle.png")


if __name__ == "__main__":
    main()