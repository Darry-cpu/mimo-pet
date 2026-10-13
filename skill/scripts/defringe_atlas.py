#!/usr/bin/env python3
"""Remove chroma-key (cyan) fringe from pet frames or an atlas, without hardening edges.

Generated strips are opaque cyan with the sprite painted on top; the extractor
derives alpha from distance-to-cyan but keeps the original RGB, so semi-transparent
edge pixels still carry cyan mix (the visible "blue edge"). This un-mixes them:
observed = a*fg + (1-a)*bg  =>  fg = (observed - (1-a)*bg) / a.
Any pixel whose decontaminated color is still near the chroma key is dropped, since
the character palette contains no cyan.

Accepts a single image or a directory (processes *.png recursively, in place).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def defringe(img: Image.Image, bg: np.ndarray, kill_dist: float) -> tuple[Image.Image, int]:
    arr = np.asarray(img.convert("RGBA")).astype(np.float64)
    rgb, alpha = arr[..., :3], arr[..., 3] / 255.0

    af = np.clip(alpha, 1e-6, 1.0)[..., None]
    fg = np.clip((rgb - (1.0 - af) * bg[None, None, :]) / af, 0, 255)

    dist = np.linalg.norm(fg - bg[None, None, :], axis=-1)
    kill = dist < kill_dist
    new_alpha = np.where(kill, 0.0, alpha)
    fg = np.where((new_alpha <= 0)[..., None], 0.0, fg)

    out = np.dstack([fg, new_alpha[..., None] * 255.0]).astype(np.uint8)
    return Image.fromarray(out, "RGBA"), int(np.count_nonzero(kill))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atlas", help="image file or directory of png frames")
    ap.add_argument("--chroma", default="#00FFFF")
    ap.add_argument("--kill-dist", type=float, default=70.0)
    ap.add_argument("--out", help="output path (single-file mode only)")
    a = ap.parse_args()

    bg = np.array([int(a.chroma[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)
    target = Path(a.atlas).expanduser().resolve()

    if target.is_dir():
        total = 0
        files = sorted(target.rglob("*.png"))
        for f in files:
            img = Image.open(f)
            fixed, killed = defringe(img, bg, a.kill_dist)
            fixed.save(f, format="PNG")
            total += killed
        print(json.dumps({"ok": True, "dir": str(target), "files": len(files),
                          "killed_fringe_pixels": total}, indent=2))
        return

    img = Image.open(target)
    fixed, killed = defringe(img, bg, a.kill_dist)
    out_path = Path(a.out).expanduser().resolve() if a.out else target
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "WEBP" if out_path.suffix.lower() == ".webp" else "PNG"
    if fmt == "WEBP":
        fixed.save(out_path, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    else:
        fixed.save(out_path, format="PNG")
    print(json.dumps({"ok": True, "file": str(out_path),
                      "killed_fringe_pixels": killed,
                      "size_bytes": out_path.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()