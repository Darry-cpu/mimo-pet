#!/usr/bin/env python3
"""Package a finished pet run into Codex and/or MiMo Desktop pet folders.

Replaces the shell + jq packaging steps so the workflow runs on Windows
without jq or bash. Writes:

  <codex-pets>/<pet-id>/pet.json + spritesheet.webp     (Codex pet contract)
  <mimo-pets>/<pet-id>/manifest.json + spritesheet.webp (MiMo Desktop Mate contract)

and refreshes qa/run-summary.json.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Per-state playback speeds derived from references/animation-rows.md durations.
# Each entry is (row, frames, fps).
ROW_SPECS = [
    ("idle", 0, 6, 6),
    ("running-right", 1, 8, 8),
    ("running-left", 2, 8, 8),
    ("waving", 3, 4, 6),
    ("jumping", 4, 5, 6),
    ("failed", 5, 8, 7),
    ("waiting", 6, 6, 6),
    ("running", 7, 6, 7),
    ("review", 8, 6, 6),
]

MIMO_ANIMATION_NAME_ALIASES = {
    # MiMo's own mate contract is only documented with `idle`; these names are
    # passed through unchanged because the state names describe the same idea.
    "running-right": "running-right",
    "running-left": "running-left",
    "running": "running",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def default_codex_pets_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "pets"
    return Path.home() / ".codex" / "pets"


def default_mimo_pets_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    user_data = Path(appdata) / "Xiaomi MiMo"
    if not user_data.is_dir():
        return None
    return user_data / "pets"


def build_codex_manifest(request: dict) -> dict:
    return {
        "id": request["pet_id"],
        "displayName": request["display_name"],
        "description": request["description"],
        "spritesheetPath": "spritesheet.webp",
    }


def build_mimo_manifest(request: dict) -> dict:
    atlas = request["atlas"]
    animations: dict[str, dict] = {}
    total_frames = 0
    for state, row, frames, fps in ROW_SPECS:
        animations[state] = {
            "row": row,
            "frames": frames,
            "fps": fps,
            "loop": True,
        }
        total_frames += frames
    return {
        "id": request["pet_id"],
        "name": request["display_name"],
        "description": request["description"],
        "version": 1,
        "spritesheet": "spritesheet.webp",
        "frameWidth": atlas["cell_width"],
        "frameHeight": atlas["cell_height"],
        "columns": atlas["columns"],
        "totalFrames": total_frames,
        "anchorX": 0.5,
        "animations": animations,
    }


def stage(
    pets_dir: Path,
    pet_id: str,
    spritesheet: Path,
    manifest_name: str,
    manifest: dict,
) -> Path:
    pet_dir = pets_dir / pet_id
    pet_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(spritesheet, pet_dir / "spritesheet.webp")
    (pet_dir / manifest_name).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return pet_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--codex-pets-dir")
    parser.add_argument("--mimo-pets-dir")
    parser.add_argument("--no-codex", action="store_true")
    parser.add_argument("--no-mimo", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    request_path = run_dir / "pet_request.json"
    spritesheet = run_dir / "final" / "spritesheet.webp"
    if not request_path.is_file():
        raise SystemExit(f"pet_request.json not found under {run_dir}")
    if not spritesheet.is_file():
        raise SystemExit(f"final/spritesheet.webp not found under {run_dir}")

    request = read_json(request_path)
    pet_id = request["pet_id"]
    written: dict[str, str] = {}

    if not args.no_codex:
        codex_pets = (
            Path(args.codex_pets_dir).expanduser().resolve()
            if args.codex_pets_dir
            else default_codex_pets_dir()
        )
        pet_dir = stage(
            codex_pets,
            pet_id,
            spritesheet,
            "pet.json",
            build_codex_manifest(request),
        )
        written["codex_package"] = str(pet_dir)
        print(f"wrote codex package: {pet_dir}")

    if not args.no_mimo:
        mimo_pets = (
            Path(args.mimo_pets_dir).expanduser().resolve()
            if args.mimo_pets_dir
            else default_mimo_pets_dir()
        )
        if mimo_pets is None:
            print(
                "skipped MiMo package: no MiMo Desktop userData directory found "
                "(pass --mimo-pets-dir to set it explicitly)",
                file=sys.stderr,
            )
        else:
            pet_dir = stage(
                mimo_pets,
                pet_id,
                spritesheet,
                "manifest.json",
                build_mimo_manifest(request),
            )
            written["mimo_package"] = str(pet_dir)
            print(f"wrote MiMo package: {pet_dir}")

    summary_path = run_dir / "qa" / "run-summary.json"
    summary = {
        "ok": True,
        "run_dir": str(run_dir),
        "spritesheet": str(spritesheet),
        "validation": str(run_dir / "final" / "validation.json"),
        "contact_sheet": str(run_dir / "qa" / "contact-sheet.png"),
        "review": str(run_dir / "qa" / "review.json"),
        "packaged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **written,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
