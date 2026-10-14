"""Regenerate ONE animation row as a single grid image so every frame keeps the
same props and details.

Rows that were originally produced as two separate sub-strips have a seam: props drawn
in one batch do not match the other (a phone that shrinks, a bowl that disappears, a
headphone that changes shape). Generating the whole row in one image removes the seam.

usage:  python regen_row.py <state>
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
SLOT_W, SLOT_H = 384, 416

IDENTITY = """chibi anime girl, WHITE hair with a small orange curl (ahoge) on top, big orange eyes, white-and-orange hooded jacket with a small orange 'mi' logo on the chest, black pleated skirt with orange trim, white sneakers with orange soles, and BIG ROUND WHITE HEADPHONES - one white earcup with a smiling black face on the side of her head facing the viewer, attached to an ORANGE antenna and headband that arc up over her head"""

COMMON = """Background: a perfectly flat uniform pure cyan #00FFFF filling the whole canvas, including behind the layout guide slots.
Keep identical in EVERY frame: the same body position, the same scale, the same ground line, the same white hair and orange curl, the same big orange eyes, the same jacket with its small orange 'mi' logo, the same skirt and sneakers, and the SAME HEADPHONES - the headphone must look exactly the same in all frames, never missing, never a different shape, size or side.
Absolutely no ground shadow, no contact shadow, no ground line, no ellipse or mark under the feet. No scenery, no text, no speed lines, no dust, no glow, no detached effects; keep cyan out of the character.
Style: clean cel-shaded chibi anime mascot, crisp outlines, flat bright colors.

COLOUR LOCK - these exact colours, in every frame:
- her eyes are LARGE and BRIGHT ORANGE (the same orange as the trim on her jacket) with black pupils - never brown, never yellow, never a different shape;
- her hair is PURE WHITE with a small ORANGE ahoge curl on top;
- her jacket is WHITE with ORANGE trim and a small orange 'mi' logo on the chest;
- her skirt is a PURE BLACK pleated skirt with a thin ORANGE stripe along the hem - never grey, never brown, never a different colour;
- her sneakers are white with ORANGE soles;
- her headphones are WHITE with an orange headband, an orange antenna and ONE black smiling face on the earcup;
- thick dark outlines around every shape, and flat two-tone cel shading - no soft gradients, no grey wash, no glossy highlights.

SIZE AND STYLE LOCK - she must be the SAME SIZE AND SHAPE as the reference character in every frame:
- Her body height is about 95% of the slot height: the tip of her ahoge sits near the top edge and the soles of her shoes sit on the bottom edge of the slot, exactly like the reference.
- The SAME head size, the SAME body width, the SAME arm and leg thickness, the SAME face proportions and the SAME flat cel shading with the same thin dark outline as the reference.
- She is NEVER drawn smaller, slimmer, younger, glossier, shinier or more detailed than the reference. Do not add strands of hair, extra fingers, extra shading gradients or highlights that the reference does not have.
- The same jacket with its orange trim (NOT a hoodie and never with a hood raised), the same black pleated skirt, the same white sneakers.
- Only the pose, the facial expression and the height of her feet change between frames."""

# state -> (grid cols, grid rows, frame count, frame list, prop rules)
ROWS = {
    "running": (
        3, 2, 6,
        """This is her WORKING animation. She sits facing the viewer at a SMALL OPEN LAPTOP placed in front of her, both hands on the keyboard, typing with a focused expression.
1. both hands resting on the keyboard, focused, ready
2. typing: left hand pressing a key, the other hand still
3. typing: both hands pressing keys
4. typing: right hand pressing a key, the other hand still
5. leaning in slightly closer to the screen, still typing
6. identical to frame 1 so the loop closes cleanly""",
        """THE LAPTOP MUST BE IDENTICAL IN EVERY FRAME. Draw it the same way all six times: a SMALL OPEN LAPTOP with a LIGHT SILVER-white body and a dark screen, the same size, the same position in front of her, the same angle, its screen always facing her. Use the LIGHT SILVER-white laptop body in ALL SIX frames - do not draw a black or dark laptop in any frame. Both her hands stay on its keyboard. The laptop must NEVER disappear, shrink, change colour, close, or move. Do not add a phone, a mug or any other prop."""
    ),
    "review": (
        3, 2, 6,
        """This is her EATING animation. She holds a small bowl in one hand and a small spoon in the other, eating happily.
1. spoon lifted up to her mouth, bowl held at chest height
2. chewing with her cheeks slightly puffed, spoon still near her mouth
3. spoon lowered towards the bowl, about to take another bite
4. spoon lifted back up to her mouth
5. chewing happily, a small closed-eye smile
6. identical to frame 1 so the loop closes cleanly""",
        """THE BOWL AND SPOON MUST BE PRESENT AND IDENTICAL IN EVERY SINGLE FRAME: the same small bowl, the same small spoon, the same size, the same colours, held in the same two hands. Neither the bowl nor the spoon may ever disappear, shrink, change colour, swap hands, or float. Both props stay close to her body and never touch the slot edges."""
    ),
    "jumping": (
        3, 2, 6,
        """This is her EXCITED JUMP: ONE single hop - crouch, launch, airborne, come down, land.
1. CROUCH: both knees bent deeply with her hips dropped low, torso leaning slightly forward, both arms swung back behind her hips, looking up eagerly with her mouth slightly open.
2. LAUNCH: both legs extended straight with her heels lifted as she stands on her toes, her body stretched as tall as she can, both arms thrown straight up above her head, big happy open-mouth smile.
3. APEX: she is AIRBORNE - both knees pulled up towards her chest and both feet lifted clearly off the ground with empty cyan below her shoes, both arms up and out to the sides, her long hair and ahoge flying upward, mouth wide open in a joyful shout.
4. DESCENT: still airborne but lower than frame 3, both legs reaching straight down with her toes pointed, both arms coming down towards her sides, eyes open with a determined closed mouth.
5. LAND: both feet flat on the ground, knees bent deeply to absorb the landing with her hips dropped low, both arms out to the sides for balance, eyes open with a small satisfied closed-mouth smile.
6. identical to frame 1 so the loop closes cleanly""",
        """CHIBI PROPORTIONS LOCK - she must have EXACTLY the reference's proportions: a HUGE chibi head (about as wide as her shoulders and roughly 45% of her total height), a SHORT torso, SHORT and THICK arms and legs, big shoes, mitten-like hands. Never draw her taller, slimmer, longer-limbed, smaller-headed or more realistic than the reference.

DETAIL LOCK - all of these must be IDENTICAL in every one of the six frames and must never flicker between frames:
- the big white headphones: ONE white earcup with a smiling black face, always on the SAME side of her head (the side facing the viewer) and at the same height in all six frames - the earcup must NEVER disappear, never move to the other side, never change shape, size or colour;
- the same orange antenna and headband arcing up over her head, and the same orange 'mi' logo hairclip, in the same place every frame;
- the same face and the same eyes in the same style (big flat orange eyes, same size and position on the head), the same flat cel shading and the same thin dark outline;
- the same white-and-orange jacket with its orange trim (never a hoodie, never with the hood raised), the same black pleated skirt with orange trim, the same white sneakers with orange soles.

VIEW LOCK: she faces the VIEWER (front view) in every single frame - the same camera angle as the reference image. Never a side view, never a three-quarter turn, never turned away: her chest, both eyes and the front of her skirt stay towards the camera in all six frames, including the airborne frame.

SIZE LOCK: her body is exactly the same size in all six frames. In the two standing frames she fills about 95% of the slot height, with the soles of her shoes on the bottom edge of the slot and the tip of her ahoge near the top edge. She jumps by tucking her legs and stretching, so she never leaves the slot.

THE HOP MUST READ AS A JUMP: her soles sit low on the ground line in frames 1, 2, 5 and 6 and clearly ABOVE it in frames 3 and 4, with an obviously empty cyan gap under her shoes in frame 3 and a smaller gap in frame 4.

Both hands are empty in every frame - no props at all. No motion lines, no dust, no sparkles, no impact marks, no ground shadow, no ground line and no ellipse of any kind under her feet."""
    ),
    "failed": (
        4, 2, 8,
        """This is her SAD / DEFLATED animation. She looks downcast and unhappy.
1. shoulders drop, arms hanging limply, mouth turned down
2. head lowers a little more
3. a small unhappy tremble, eyes slightly squeezed
4. eyes welling up, very sad
5. a long sigh, body sinking slightly
6. head still down, ears of hair drooping
7. still sad but slowly lifting her head a little
8. identical to frame 1 so the loop closes cleanly""",
        """SIZE LOCK - THIS IS CRITICAL: her body must be EXACTLY THE SAME SIZE in all eight frames. Same height, same width, same distance from the camera, same scale - she must fill the same amount of the slot every time. Do NOT shrink her, do NOT zoom out, do NOT draw her smaller in any frame; only her pose, her head angle and her facial expression change. Her feet stay on the same ground line in every frame.
No props at all - both hands stay empty and hang at her sides. The headphone must not change shape between frames: the earcup keeps the same smiling black face and the orange antenna keeps exactly the same shape and position in all eight frames."""
    ),
}


def api_key() -> str:
    t = CFG.read_text(encoding="utf-8")
    lines = [l for l in t.split("\n") if not l.strip().startswith("//")]
    clean = re.sub(r",(\s*[}\]])", r"\1", "\n".join(lines))
    return json.loads(clean)["provider"]["alibaba-cn"]["options"]["apiKey"]


def data_url(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def build_guide(cols: int, rows: int, out: Path) -> Path:
    img = Image.new("RGB", (SLOT_W * cols, SLOT_H * rows), CHROMA)
    d = ImageDraw.Draw(img)
    for r in range(rows):
        for c in range(cols):
            d.rectangle([c * SLOT_W + 10, r * SLOT_H + 10,
                         (c + 1) * SLOT_W - 10, (r + 1) * SLOT_H - 10],
                        outline=(120, 120, 120), width=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


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
    import argparse
    ap = argparse.ArgumentParser(description="regenerate one animation row")
    ap.add_argument("state")
    ap.add_argument("--ref", action="append", default=[],
                    help="extra reference image (e.g. the character sheet), repeatable")
    a = ap.parse_args()
    state = a.state
    if state not in ROWS:
        raise SystemExit(f"unknown state {state!r}; known: {', '.join(ROWS)}")
    cols, rows, count, frames, prop_rules = ROWS[state]

    canonical = RUN / "references" / "canonical-base.png"
    img_w, img_h = SLOT_W * cols, SLOT_H * rows

    extra = [Path(r).expanduser().resolve() for r in a.ref]
    refs = [canonical] + extra
    sheet_note = ("\nThe last attached image is the official character sheet: take her COLOURS and her "
                  "design from it exactly (white hair, ORANGE eyes, white-and-orange jacket, BLACK pleated "
                  "skirt with orange trim, white sneakers with orange soles, white headphones with an orange "
                  "headband)." if extra else "")
    prompt = f"""Using the second attached image as the EXACT character identity reference ({IDENTITY}), draw ONE single image containing exactly {count} full-body frames of the SAME character, arranged in a {cols}x{rows} grid.{sheet_note}

Grid order: read left to right, top row first.
The first attached image is a layout guide: follow the slot positions, but DO NOT draw the guide's boxes, lines or background.

{frames}

{prop_rules}

{COMMON}"""

    guide = build_guide(cols, rows, RUN / "references" / "layout-guides" / f"{state}-{cols}x{rows}.png")
    raw = RUN / "tmp" / f"{state}-grid-raw.png"
    print(f"generating {state} as a {cols}x{rows} grid ({count} frames) ...", flush=True)
    generate(prompt, [guide] + refs, f"{img_w}*{img_h}", raw)

    img = Image.open(raw).convert("RGB")
    print("raw returned:", img.size)
    if img.size != (img_w, img_h):
        img = img.resize((img_w, img_h), Image.Resampling.LANCZOS)

    cells = [img.crop((c * SLOT_W, r * SLOT_H, (c + 1) * SLOT_W, (r + 1) * SLOT_H))
             for r in range(rows) for c in range(cols)]

    strip = Image.new("RGB", (SLOT_W * len(cells), SLOT_H), CHROMA)
    for i, cell in enumerate(cells):
        strip.paste(cell, (i * SLOT_W, 0))
    strip_path = RUN / "tmp" / f"{state}-strip.png"
    strip.save(strip_path)
    for i, cell in enumerate(cells):
        cell.save(RUN / "tmp" / f"{state}-cell-{i}.png")
    print(f"assembled strip {strip.size}")

    subprocess.run([sys.executable, str(SKILL / "scripts" / "mark_job_complete.py"),
                    "--run-dir", str(RUN), "--job-id", state,
                    "--source", str(strip_path)], check=True)
    print("staged ->", RUN / "decoded" / f"{state}.png")


if __name__ == "__main__":
    main()