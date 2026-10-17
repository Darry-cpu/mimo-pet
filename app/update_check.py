# -*- coding: utf-8 -*-
"""检查有没有新版本 —— 只读地问一下 GitHub，不带任何个人信息。

什么时候查：
  * 程序启动后静默查一次（**一天最多真查一次**，其余时候直接用上次的结果）；
  * 或右键菜单「检查更新」，随叫随查。

查到了不自动下载、不自动安装 —— 只用气泡告诉她，「点气泡」打开下载页面。
网络不通、超时、GitHub 抽风都当成"这次没查到"，绝不打扰正常使用。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Darry-cpu/mimo-pet"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
FALLBACK_URL = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 8.0                    # 秒 —— 超时就当没查到，绝不把程序卡住
CHECK_INTERVAL = 24 * 3600.0     # 自动检查的间隔：一天
STATE_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MimoPet" / "update.json"

#: check() 的返回值
NEWER, CURRENT, ERROR = "newer", "current", "error"


def parse_version(text: str) -> tuple[int, ...]:
    """"v1.1.21" / "1.1.21" → (1, 1, 21)；解析不出来就是空元组。"""
    return tuple(int(part) for part in text.strip().lstrip("vV").split(".") if part.isdigit())


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新。任一边解析不出来就判 False（宁可不提醒）。"""
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))          # 补齐长度，"1.2" 才比得过 "1.2.0"
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError:
        logging.debug("更新状态写不进去", exc_info=True)


def fetch_latest() -> tuple[str, str] | None:
    """问一次 GitHub：返回 (版本号, 网页地址)，查不到返回 None。"""
    req = urllib.request.Request(API_URL, headers={
        "User-Agent": "MimoPet-UpdateCheck",       # GitHub 要求带 UA，否则 403
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except (OSError, ValueError) as exc:            # URLError/超时/JSON 坏都算没查到
        logging.info("查更新失败：%s", exc)
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not parse_version(tag):
        return None
    return tag, str(data.get("html_url") or "") or FALLBACK_URL


def check(current: str, force: bool = False) -> tuple[str, str, str]:
    """查一次更新，返回 (状态, 版本号, 网页地址)。

    状态是 ``NEWER``（有新版本）、``CURRENT``（已经最新）或 ``ERROR``（没查到）。
    ``force=False`` 时一天最多真查一次，其余时候直接拿上次的结果。
    """
    state = _load_state()
    now = time.time()
    last = float(state.get("checked_at") or 0.0)
    cached = str(state.get("latest") or "")
    cached_url = str(state.get("url") or "") or FALLBACK_URL

    if not force and now - last < CHECK_INTERVAL:
        if cached and is_newer(cached, current):
            return NEWER, cached, cached_url
        return CURRENT, current, FALLBACK_URL

    got = fetch_latest()
    if got is None:
        _save_state({**state, "checked_at": now})   # 记下这次查过，免得一天内反复请求
        return ERROR, current, FALLBACK_URL

    tag, url = got
    _save_state({"checked_at": now, "latest": tag, "url": url})
    if is_newer(tag, current):
        return NEWER, tag, url
    return CURRENT, current, url
