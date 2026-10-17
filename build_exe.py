#!/usr/bin/env python3
"""Build the single-file MimoPet.exe that mimo_setup.iss packages.

Requires: Python 3.10+, Pillow, and  pip install pyinstaller

    python build_exe.py

Output: dist/MimoPet.exe  (run  iscc mimo_setup.iss  afterwards to build the installer)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "app"
DIST = REPO / "dist"
BUILD = REPO / "build"
EXE = DIST / "MimoPet.exe"


def _version(marker: str, path: Path, pattern: str) -> str:
    """从某个文件里抠出版本号，抠不到就报错。"""
    text = path.read_text(encoding="utf-8-sig")
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise SystemExit(f"{marker}: 在 {path.name} 里找不到版本号（模式 {pattern}）")
    return found.group(1)


def check_versions() -> str:
    """程序里的 APP_VERSION 必须和安装脚本的 AppVersion 一致。

    这条检查是有来历的：改完源码忘了重新编译，装机上跑的还是旧版本，
    用户报"还是没修好"，白绕一大圈。让编译直接失败，比事后查时间戳可靠。
    """
    app_version = _version("app/mimo_pet.py", APP / "mimo_pet.py",
                           r'^APP_VERSION\s*=\s*"([^"]+)"')
    iss_version = _version("mimo_setup.iss", REPO / "mimo_setup.iss",
                           r'#define\s+AppVersion\s+"([^"]+)"')
    if app_version != iss_version:
        raise SystemExit(
            "版本号不一致，编译中止：\n"
            f"  app/mimo_pet.py 的 APP_VERSION = {app_version!r}\n"
            f"  mimo_setup.iss 的 AppVersion  = {iss_version!r}\n"
            "两个都要改成同一个新版本号再编译（更新提醒靠它判断新旧）。")
    print(f"版本号一致：{app_version}")
    return app_version


def main() -> None:
    if not (APP / "mimo_pet.py").is_file():
        raise SystemExit("app/mimo_pet.py not found")
    for required in ("assets/spritesheet.webp", "assets/animations-extra.webp"):
        if not (REPO / required).is_file():
            raise SystemExit(required + " not found")

    check_versions()
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "MimoPet",
        "--icon", str(APP / "mimo.ico"),
        "--add-data", f"{REPO / 'assets' / 'spritesheet.webp'};.",
        "--add-data", f"{REPO / 'assets' / 'animations-extra.webp'};.",
        "--add-data", f"{REPO / 'assets' / 'speech-bubble.png'};.",
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