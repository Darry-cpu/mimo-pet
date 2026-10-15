#!/usr/bin/env python3
"""Build the single-file MimoPet.exe that mimo_setup.iss packages.

Requires: Python 3.10+, Pillow, and  pip install pyinstaller

    python build_exe.py

Output: dist/MimoPet.exe  (run  iscc mimo_setup.iss  afterwards to build the installer)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "app"
DIST = REPO / "dist"
BUILD = REPO / "build"
EXE = DIST / "MimoPet.exe"


def main() -> None:
    if not (APP / "mimo_pet.py").is_file():
        raise SystemExit("app/mimo_pet.py not found")
    for required in ("assets/spritesheet.webp", "assets/animations-extra.webp"):
        if not (REPO / required).is_file():
            raise SystemExit(required + " not found")

    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "MimoPet",
        "--icon", str(APP / "mimo.ico"),
        "--add-data", f"{REPO / 'assets' / 'spritesheet.webp'};.",
        "--add-data", f"{REPO / 'assets' / 'animations-extra.webp'};.",
        "--add-data", f"{APP / 'mimo.ico'};.",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        str(APP / "mimo_pet.py"),
    ]
    print("running:", " ".join(cmd[-12:]), "\n")
    subprocess.run(cmd, check=True)

    if not EXE.is_file():
        raise SystemExit("build did not produce dist/MimoPet.exe")
    print(f"\ndone -> {EXE}  ({EXE.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()