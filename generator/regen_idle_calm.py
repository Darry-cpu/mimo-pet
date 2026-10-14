"""Regenerate the idle row as SIX calm standing frames - no running, no props.

Why: the idle row previously had run frames and phone/eating poses baked into it, so
whenever the pet was idle it looked like it was running in place ("假跑"). Now the idle
row is only a calm breathing / blinking loop, and the app drives phone, working, eating
and running as separate states with their own triggers.

Frames (the app plays row 0, columns 0-5, in this order):
  0 calm stand            5 calm stand, tiny head tilt
  1 calm stand, breathe out (a very small settle)
  2 blink (eyes closed)
  3 calm stand, breathe in (a very small rise)
  4 calm stand
  5 identical to frame 0 so the loop closes exactly
"""

from __future__ import annotations

import os
import base64
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

RUN = Path(os.environ.get("MIMO_PET_RUN", "pet-run"))
SKILL = Path(os.environ.get("MIMO_SKILLS", "hatch-pet"))
CFG = Path(os.environ.get("MIMO_CONFIG", "mimocode.jsonc"))
MODEL = "qwen-image-2.0-pro-2026-06-22"
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

CHROMA = (0, 255, 255)
SLOT_W, SLOT_H = 384, 416
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
    img = Image.new("RGB", (IMG_W, IMG_H), CHROMA)
    d = ImageDraw.Draw(img)
    for r in range(ROWS):
        for c in range(COLS):
            d.rectangle([c * SLOT_W + 10, r * SLOT_H + 10,
                         (c + 1) * SLOT_W - 10, (r + 1) * SLOT_H - 10],
                        outline=(120, 120, 120), width=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


PROMPT = """Using the second attached image as the EXACT character identity reference, draw ONE single image containing exactly SIX full-body frames of the SAME character, arranged in a 3x2 grid.

This is a CALM STANDING IDLE animation. She is simply standing still, facing the viewer, with both arms relaxed down at her sides. The only things that change between frames are very small: gentle breathing, a tiny up-and-down bob, and one blink.

Grid order: top row left-to-right = frames 1,2,3. Bottom row left-to-right = frames 4,5,6.
The first attached image is a layout guide: follow the slot positions, but DO NOT draw the guide's boxes, lines or background.

Frame contents (differences must be very small - this is a subtle idle, not an action):
1. calm standing, relaxed, eyes open, slight smile - the base pose
2. same pose, breathing out: body very slightly settled/lower
3. same pose, eyes closed for a blink (everything else identical to frame 1)
4. same pose, breathing in: body very slightly raised
5. calm standing, a tiny head tilt of just a few degrees
6. identical to frame 1, so the loop ends exactly where it started

IMPORTANT - she must NOT run, walk, jump, wave, hold a phone, hold food, or hold any prop in any of the six frames. Both hands stay empty and relaxed at her sides. Her feet stay in the same place in every frame.

Keep identical in every frame: the same body position, the same scale, the same ground line, the same white hair with the orange top curl, the same big orange eyes, the same white-and-orange hooded jacket, the same black pleated skirt with orange trim, the same white sneakers with orange soles, and the SAME BIG ROUND WHITE HEADPHONES (a white earcup with a smiling black face on an orange antenna arcing over her head) - the headphone must look exactly the same on every frame.

Background: a perfectly flat uniform pure cyan #00FFFF filling the whole canvas, including behind the layout guide slots.
Absolutely no ground shadow, no contact shadow, no ground line, no ellipse or mark under the feet.
No scenery, no text, no speed lines, no dust, no glow, no detached effects; keep cyan out of the character.
Style: clean cel-shaded chibi anime mascot, crisp outlines, flat bright colors."""


def generate(prompt: str, images: list[Path], size: str, out: Path) -> Path:
    key = api_key()
    content = [{"image": data_url(p)} for p in images]
    content.append({"text": prompt})
    payload = {"model": MODEL,
               "input": {"messages": [{"role": "user", "content": content}]},
               "parameters": {"n": 1, "size": size}}
    for attempt in range(8):
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST")
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
    canonical = RUN / "references" / "canonical-base.png"
    if not canonical.is_file():
        raise SystemExit(f"missing canonical base: {canonical}")

    guide = build_guide(RUN / "references" / "layout-guides" / "idle-calm-3x2.png")
    raw = RUN / "tmp" / "idle-calm-grid-raw.png"
    print("generating 3x2 calm idle grid ...", flush=True)
    generate(PROMPT, [guide, canonical], f"{IMG_W}*{IMG_H}", raw)

    img = Image.open(raw).convert("RGB")
    print("raw returned:", img.size)
    if img.size != (IMG_W, IMG_H):
        img = img.resize((IMG_W, IMG_H), Image.Resampling.LANCZOS)

    cells = [img.crop((c * SLOT_W, r * SLOT_H, (c + 1) * SLOT_W, (r + 1) * SLOT_H))
             for r in range(ROWS) for c in range(COLS)]

    strip = Image.new("RGB", (SLOT_W * len(cells), SLOT_H), CHROMA)
    for i, cell in enumerate(cells):
        strip.paste(cell, (i * SLOT_W, 0))
    strip_path = RUN / "tmp" / "idle-strip.png"
    strip.save(strip_path)
    for i, cell in enumerate(cells):
        cell.save(RUN / "tmp" / f"idle-calm-cell-{i}.png")
    print(f"assembled strip {strip.size}")

    subprocess.run([sys.executable, str(SKILL / "scripts" / "mark_job_complete.py"),
                    "--run-dir", str(RUN), "--job-id", "idle",
                    "--source", str(strip_path)], check=True)
    print("staged ->", RUN / "decoded" / "idle.png")


if __name__ == "__main__":
    main()