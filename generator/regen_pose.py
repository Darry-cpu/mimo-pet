#!/usr/bin/env python3
"""Redraw ONE pose from an existing frame, so the character keeps the atlas style.

Generating a whole row from a text prompt drifts: the model redraws her slimmer, glossier
or at a different scale than the rows already in the atlas. This tool instead hands the
model a frame we already trust and asks it to change ONLY the pose, which keeps the size,
the shading, the outfit and the ground line consistent with the rest of the atlas.

usage:
    python regen_pose.py --pose "crouching ..." --out pose.png [--base frame.png]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

RUN = Path(os.environ.get("MIMO_PET_RUN", "pet-run"))
CFG = Path(os.environ.get("MIMO_CONFIG", "mimocode.jsonc"))
MODEL = "qwen-image-2.0-pro-2026-06-22"
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
CHROMA = "a perfectly flat uniform pure cyan #00FFFF"

KEEP = """This is the character reference. Keep her EXACTLY as she is and change ONLY her pose:
- the SAME body size and the same height in the slot, the SAME head size, the SAME body width, the SAME arm and leg thickness - do not shrink her, do not zoom in or out, do not move her up or down;
- the SAME art style: the same clean cel shading with flat bright colours, the same thin dark outline, the same face proportions - no extra gradients, no glossy highlights, no extra hair strands, no extra fingers;
- the SAME chibi proportions: she is a CHIBI mascot - her head is huge (about as wide as her shoulders and roughly 45% of her total height), her torso is SHORT, her arms and legs are SHORT and THICK with big shoes and mitten-like hands. Never draw her taller, slimmer, longer-limbed or more realistic; keep the head-to-body ratio exactly as in the reference;
- the SAME outfit: the same white-and-orange jacket with its small orange 'mi' logo (never a raised hood), the same black pleated skirt with orange trim, the same white sneakers with orange soles, the same big white headphones with the smiling black face on the earcup;
- the SAME camera angle: full body, front view, feet towards the bottom of the image;
- the background stays """ + CHROMA + """ filling everything around her, and there is NO shadow, NO ground line, NO ellipse and no scenery of any kind."""


def api_key() -> str:
    t = CFG.read_text(encoding="utf-8")
    lines = [l for l in t.split("\n") if not l.strip().startswith("//")]
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "\n".join(lines)))["provider"]["alibaba-cn"]["options"]["apiKey"]


def data_url(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def generate(prompt: str, images: list[Path], size: str, out: Path) -> Path:
    key = api_key()
    content = [{"image": data_url(p)} for p in images]
    content.append({"text": prompt})
    payload = {"model": MODEL,
               "input": {"messages": [{"role": "user", "content": content}]},
               "parameters": {"n": 1, "size": size}}
    for attempt in range(8):
        req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"}, method="POST")
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pose", required=True, help="what she should be doing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=None, help="frame to edit (default: canonical-base.png)")
    ap.add_argument("--size", default="1024*1024")
    a = ap.parse_args()

    base = Path(a.base).expanduser().resolve() if a.base else RUN / "references" / "canonical-base.png"
    prompt = f"{KEEP}\n\nHer new pose: {a.pose}\n\nEverything else stays exactly as in the reference."
    out = Path(a.out).expanduser().resolve()
    print(f"generating pose -> {out.name}", flush=True)
    generate(prompt, [base], a.size, out)
    img = Image.open(out)
    print(f"  returned {img.size}")


if __name__ == "__main__":
    main()