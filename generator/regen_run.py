"""Regenerate the running-right row as ONE 4x2 generation.

Problems being fixed:
  * headphones flicker - the 8 frames were generated in two halves, so the headphone
    was drawn inconsistently between them (visible on some frames, missing on others)
  * generating the whole row in a single image keeps the headphone, face, palette and
    scale identical on every frame

Layout: 4 columns x 2 rows of equal slots, each slot with the same aspect ratio as the
192x208 cell, so nothing is distorted when the frames are extracted.
"""

from __future__ import annotations

import os
import base64
import json
import re
import subprocess
import sys
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

CHROMA = (0, 255, 255)
CHROMA_HEX = "#00FFFF"
SLOT_W, SLOT_H = 384, 416
COLS, ROWS = 4, 2
IMG_W, IMG_H = SLOT_W * COLS, SLOT_H * ROWS      # 1536 x 832


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


PROMPT = """Using the second attached image as the EXACT character identity reference, draw ONE single image containing exactly EIGHT full-body frames of the SAME character, arranged in a 4x2 grid.

This is a SIDE-VIEW RUN TO THE RIGHT. Every frame shows her in profile, facing and travelling to the RIGHT.

Grid order: top row left-to-right = frames 1,2,3,4. Bottom row left-to-right = frames 5,6,7,8.
The first attached image is a layout guide: follow the slot positions, but DO NOT draw the guide's boxes, lines or background.

Run cycle - THE TWO LEGS MUST ALTERNATE. This is the most important requirement:
1. contact - the NEAR leg (closer to the camera) reaches forward and plants; the far leg trails behind
2. down - weight on that planted near leg, body a little lower
3. pass - the far leg swings forward past the near leg, one knee lifted
4. up - pushing off, body a little higher
5. contact - now the FAR leg is forward and planted; the near leg trails behind  <-- OPPOSITE leg to frame 1
6. down - weight on the far planted leg, body a little lower
7. pass - the near leg swings forward past the far leg, knee lifted
8. up - push off, body a little higher, ready to loop back to frame 1

So frames 1-4 lead with one leg and frames 5-8 lead with the OTHER leg. Do NOT draw the same leading leg on all eight frames - that would look like hopping on one leg instead of running.

CRITICAL - the headphones must be identical on EVERY frame. She wears big round WHITE headphones: one earcup is clearly visible on the side of her head facing the viewer, drawn as a white circle with a smiling black face, attached to an ORANGE antenna / headband that arcs up over her head. The other earcup sits on the far side of her head and stays partly visible above her hair. Never omit the headphone, never let it fade, shrink, change shape or disappear - it must look exactly the same in all eight frames.

Also keep identical in every frame: the same body size and scale, the same ground line, the same white hair and orange top curl, the same big orange eyes, the same white-and-orange hooded jacket, the same black pleated skirt with orange trim, the same white sneakers with orange soles. Only the legs and arms change to make the run.

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

    guide = build_guide(RUN / "references" / "layout-guides" / "running-right-4x2.png")
    raw = RUN / "tmp" / "running-right-grid-raw.png"
    print("generating 4x2 running-right grid ...", flush=True)
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
    strip_path = RUN / "tmp" / "running-right-strip.png"
    strip.save(strip_path)
    for i, cell in enumerate(cells):
        cell.save(RUN / "tmp" / f"running-right-cell-{i}.png")
    print(f"assembled strip {strip.size}")

    subprocess.run([sys.executable, str(SKILL / "scripts" / "mark_job_complete.py"),
                    "--run-dir", str(RUN), "--job-id", "running-right",
                    "--source", str(strip_path)], check=True)
    print("staged ->", RUN / "decoded" / "running-right.png")


if __name__ == "__main__":
    main()