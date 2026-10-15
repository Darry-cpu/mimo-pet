#!/usr/bin/env python3
"""Mimo desktop pet - a standalone walking pet for Windows.

The ChatGPT desktop app can only play an in-place animation while you drag the pet;
it never moves the pet on its own. This program draws the SAME spritesheet in its own
always-on-top layered window and actually translates that window across the desktop,
so the pet really walks left and right.

Animation timings follow the official pet contract (the same durations the app uses),
so the motion looks natural rather than sped up.

Controls
  left-drag    move the pet (behaviour pauses while you hold it)
  right-click  menu: pause/resume, size, quit
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import random
import shutil
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- configuration
CELL_W, CELL_H = 192, 208
DEFAULT_SCALE = 1.0
SCALES = [0.75, 1.0, 1.25, 1.5]

# frame counts and per-frame durations, matching references/animation-rows.md
STATES = {
    "idle":          (0, 6, [1680, 660, 660, 840, 840, 1920]),
    "running-right": (1, 8, [120] * 7 + [220]),
    "running-left":  (2, 8, [120] * 7 + [220]),
    "waving":        (3, 4, [140, 140, 140, 280]),
    "jumping":       (4, 6, [150, 140, 140, 150, 140, 240]),
    "failed":        (5, 8, [140] * 7 + [240]),
    "waiting":       (6, 6, [150] * 5 + [260]),
    "running":       (7, 6, [120] * 5 + [220]),
    "review":        (8, 6, [150] * 5 + [280]),
}

# 附加图集（assets/animations-extra.webp）里的动作：行号属于那张图，不占 Codex 契约的 8x9
# 帧时长是"慢放前"的基准（休息动作还要乘 WAIT_FACTOR=2.4）：
# 动作时帧率正常（每帧 0.2~0.3s），"安静"靠轮间更长的停顿实现，而不是把帧拖慢
EXTRA_STATES = {
    "daze":  (0, 6, [180] * 5 + [300]),      # 发呆（每帧约 0.43s）
    "shake": (1, 6, [160] * 5 + [280]),      # 摇头（每帧约 0.38s）
    "music": (2, 6, [190] * 5 + [320]),      # 听歌（每帧约 0.46s）
}
ALL_STATES = {**STATES, **EXTRA_STATES}
# 休息时段轮流出现的动作（以前只有玩手机 / 干活中）
REST_STATES = ["waiting", "running", "daze", "shake", "music"]

# ---------------------------------------------------------------- behaviour script
# 触发规则（按用户指定）：
#   单击        -> 投喂     -> 吃饭中
#   双击        -> 抚摸     -> 高兴地跳
#   鼠标碰到不点 -> 打招呼   -> 挥手
#   频繁挪动     -> 被弄烦了 -> 沮丧
# 平时无人打扰：玩手机 / 干活中 各保持 30 秒，然后从一侧跑到另一侧。
CLICK_REACTIONS = [["review", "review"]]                 # 投喂 -> 吃饭中
DBLCLICK_REACTIONS = [["jumping", "jumping"]]            # 抚摸 -> 高兴跳
HOVER_REACTIONS = [["waving"]]                           # 打招呼
GRUMPY_REACTION = ["failed", "failed"]                   # 沮丧

FPS = 60

# pacing - the app's own frame timings feel rushed for a desktop pet, so the
# non-idle animations are played slower here; the walk speed is matched to the
# slowed run cycle so her feet do not slide on the ground
STROLL_FACTOR = 1.6         # animation slowdown for standing actions & strolling
DASH_FACTOR = 1.0           # animation speed while crossing the screen
WAIT_FACTOR = 2.4           # 玩手机再放慢一档，刷手机本来就该慢悠悠
WAIT_HOLD_MIN = 2.2         # 每播完一轮玩手机，停在原地“看屏幕”一会儿
WAIT_HOLD_MAX = 4.8         # （像认真看内容，而不是无限鬼畜循环）
STROLL_SPEED = 78.0         # px per second while strolling (matches the run cycle)
DASH_SPEED = 200.0          # px per second while crossing the screen
REST_SECONDS = 30.0         # how long she stays on 玩手机 / 干活中 between trips
TRAVEL_MIN = 220.0          # px - shortest stroll between rests
TRAVEL_MAX = 620.0          # px - longest stroll (does not need to cross the screen)

# 拖拽甩抛：跟手用"过阻尼弹簧"（跟得上、不抖、不过冲），松手快了就沿抛物线飞出去
DRAG_FOLLOW_TAU = 0.05      # s - 弹簧跟手的时间常数，越小越紧跟
THROW_MIN_SPEED = 320.0     # px/s - 松手速度低于此值＝轻轻放下，不起飞
MAX_THROW_SPEED = 3500.0    # px/s - 甩速上限，防止飞出天际
GRAVITY = 2400.0            # px/s^2
BOUNCE_WALL = 0.55          # 撞左右/上边界后保留的速度比例
BOUNCE_GROUND = 0.34        # 落地保留的速度比例（越大弹得越欢）
GROUND_FRICTION = 0.80      # 每次触地水平速度保留比例（摩擦力）
AIR_DRAG = 0.55             # 每秒空气阻力，v *= AIR_DRAG ** dt
SETTLE_SPEED = 45.0         # px/s - 慢于此且在地上＝停稳，回归日常
FLY_RUN_SPEED = 140.0       # px/s - 飞行中横向快于此值就放跑步动画

# 拖拽时挤进屏幕边框 -> 朝屏幕内弹一下（一次冲量 + 弹簧衰减，肉眼能看到 1~2 次回弹）
KNOCK_KICK = 9.0            # 每"挤进边框 1px"给出的初速度 (px/s)
KNOCK_STIFF = 240.0         # 回弹弹簧（刚度 -> 一个来回约 0.41 秒）
KNOCK_DAMP = 7.0            # 阻尼（欠阻尼，能看到弹两次再停下）

# 置顶看门狗：WS_EX_TOPMOST 只是"样式位"，窗口被别的窗口扰动后可能仍带位却沉到下面，
# 必须偶尔真调一次 SetWindowPos(HWND_TOPMOST) 才能回到置顶带（dsh-pet 踩过这个坑）
TOPMOST_TICK = 1.0          # s - 多久检查一次
TOPMOST_REASSERT = 5.0      # s - 最快多久真重设一次（低频，不打扰别人）

BODY_ALPHA_MIN = 32         # alpha 高于此值才算"身体"，用于判断鼠标是否真的碰到她

# 右键"动作"子菜单：命令号从 CMD_ACTION 起，避免和固定项（1-6）撞车
CMD_ACTION = 100
ACTION_MENU = ["idle", "waving", "jumping", "failed", "waiting", "running", "review",
              "daze", "shake", "music"]
STATE_LABELS = {
    "idle": "待机", "running-right": "向右跑", "running-left": "向左跑",
    "waving": "挥挥手", "jumping": "跳一下", "failed": "沮丧一下",
    "waiting": "玩手机", "running": "干活中", "review": "吃点东西",
    "daze": "发呆", "shake": "摇头", "music": "听歌",
}

# interactions
DRAG_WINDOW = 30.0          # seconds - window used to count "shoved around a lot"
DRAG_LIMIT = 3              # drags within the window before she gets grumpy
HOVER_PET_SECONDS = 1.2     # cursor resting on her this long = 打招呼
CLICK_DELAY = 0.28          # wait this long to be sure a lone click is not a double click

def _resource(name: str) -> Path:
    """Path to a bundled file, whether running from source or from a one-file exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent / name


HERE = Path(__file__).resolve().parent
SHEET = _resource("spritesheet.webp")
SHEET2 = _resource("animations-extra.webp")
ICON = _resource("mimo.ico")

_data_dir = Path(os.environ.get("LOCALAPPDATA", HERE)) / "MimoPet"
try:
    _data_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    _data_dir = HERE
LOG = _data_dir / "mimo_pet.log"

logging.basicConfig(filename=LOG, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# render crisply on high-DPI displays instead of being bitmap-scaled
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor v2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

def _clean_stale_runtime_dirs() -> None:
    """Delete _MEIxxxx leftovers from earlier runs of this app.

    A one-file build unpacks itself into the temp folder on every launch and only
    removes that folder on a clean exit, so every hard kill or update leaves ~56MB
    behind. Ours are told apart by the data files they carry (the atlas and the icon),
    so no other program's folder is ever touched, and the folder we are running from
    is skipped.
    """
    if not getattr(sys, "frozen", False):
        return
    root = os.environ.get("TEMP")
    here = getattr(sys, "_MEIPASS", "")
    if not root or not here:
        return
    here = Path(here)
    removed = 0
    for cand in Path(root).glob("_MEI*"):
        try:
            if not cand.is_dir() or cand.samefile(here):
                continue
            if not ((cand / "spritesheet.webp").is_file() and (cand / "mimo.ico").is_file()):
                continue
        except OSError:
            continue
        shutil.rmtree(cand, ignore_errors=True)
        if not cand.exists():
            removed += 1
            logging.info("removed leftover runtime dir %s", cand.name)
    if removed:
        logging.info("cleaned %d leftover runtime dir(s)", removed)


def _display_scale() -> float:
    """Primary display DPI factor: 1.0 = 100%, 1.5 = 150%.

    Windows scales every UI element by this factor, so the pet has to be scaled the
    same way or it looks small on a scaled display (at 150% the same 192px cell has to
    be drawn 1.5x bigger to look the way it does at 100%).
    """
    try:
        dpi = u32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    try:
        dc = u32.GetDC(None)
        dpi = g32.GetDeviceCaps(dc, 88)          # LOGPIXELSX
        u32.ReleaseDC(None, dc)
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


# ---------------------------------------------------------------- win32 plumbing
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
MF_ZOOM_OFF = 0x3            # MF_GRAYED | MF_DISABLED

u32.GetDC.restype = wintypes.HDC
u32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
u32.RegisterClassExW.argtypes = (ctypes.c_void_p,)
u32.LoadCursorW.restype = wintypes.HANDLE
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.DefWindowProcW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
u32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint)
u32.UpdateLayeredWindow.restype = wintypes.BOOL
u32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
u32.TrackPopupMenu.restype = ctypes.c_int
u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
u32.IsWindowVisible.argtypes = (wintypes.HWND,)
u32.LoadImageW.restype = wintypes.HANDLE
u32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint)
u32.SendMessageW.restype = ctypes.c_ssize_t
u32.SendMessageW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
u32.SystemParametersInfoW.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
u32.GetCursorPos.argtypes = (ctypes.c_void_p,)
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
g32.CreateCompatibleDC.restype = wintypes.HDC
g32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
g32.CreateDIBSection.restype = wintypes.HBITMAP
g32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD)
g32.SelectObject.restype = wintypes.HGDIOBJ
g32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
g32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
g32.DeleteDC.argtypes = (wintypes.HDC,)

WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
SW_SHOWNOACTIVATE = 4
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
SPI_GETWORKAREA = 0x0030
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
MF_POPUP = 0x0010
TPM_RETURNCMD = 0x0100

WM_DESTROY, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0002, 0x0201, 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEMOVE, WM_RBUTTONUP = 0x0200, 0x0205
WM_COMMAND = 0x0111
WM_QUIT = 0x0012
CS_DBLCLKS = 0x0008

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", ctypes.c_uint),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT)]


def premultiplied_bgra(img: Image.Image) -> bytes:
    a = np.asarray(img.convert("RGBA")).astype(np.uint16)
    alpha = a[..., 3]
    rgb = a[..., :3] * alpha[..., None] // 255
    return np.dstack([rgb[..., 2], rgb[..., 1], rgb[..., 0], alpha]).astype(np.uint8).tobytes()


MAX_CACHED_LEVELS = 3         # 同时缓存的缩放档位上限（每档都不小，尤其 150% 屏）

_sheet_cache: dict[Path, Image.Image] = {}


def _sheet(path: Path | None = None) -> Image.Image:
    """An atlas, decoded once and shared by every zoom level."""
    path = path or SHEET          # resolved at call time, so re-pointing SHEET works
    img = _sheet_cache.get(path)
    if img is None:
        img = Image.open(path).convert("RGBA")
        expected = ((8 * CELL_W, 3 * CELL_H) if path == SHEET2
                    else (8 * CELL_W, 9 * CELL_H))
        if img.size != expected:
            logging.warning("unexpected atlas size %s (expected %s) in %s",
                            img.size, expected, path.name)
        _sheet_cache[path] = img
    return img


class _LazyFrames:
    """Frames of one zoom level, rendered per animation the first time it is asked for.

    Rendering a whole level costs a few hundred ms (57 frames), which used to stall the
    click that asked for it. Renders happen per animation instead, so a zoom only pays
    for the pose on screen and everything else fills in as it is used.
    """

    def __init__(self, pet: "Pet", scale: float) -> None:
        self._pet = pet
        self.scale = scale
        self.last_used = time.perf_counter()
        self._cache: dict[str, list[bytes]] = {}
        self._lock = threading.Lock()

    def __getitem__(self, name: str) -> list[bytes]:
        frames = self._cache.get(name)
        if frames is None:
            with self._lock:
                frames = self._cache.get(name)
                if frames is None:
                    frames = self._pet._render_state(name, self.scale)
                    self._cache[name] = frames
        return frames

    def get(self, name: str, default=None):
        if name in ALL_STATES:
            return self[name]
        return default

    def __contains__(self, name: str) -> bool:
        return name in ALL_STATES

    def __len__(self) -> int:
        return len(ALL_STATES)

    def keys(self):
        return ALL_STATES.keys()

    def items(self):
        self.last_used = time.perf_counter()
        return [(name, self[name]) for name in ALL_STATES]

    def cached_bytes(self) -> int:
        return sum(len(f) for frames in self._cache.values() for f in frames)


class Pet:
    def __init__(self, scale: float = DEFAULT_SCALE) -> None:
        if not SHEET.is_file():
            raise SystemExit(f"missing spritesheet: {SHEET}")
        # 缩放分两层：rel_scale 是菜单里的档位，scale 是这屏幕上的真实大小
        self.dpi = _display_scale()
        self.rel_scale = scale
        self.scale = scale * self.dpi
        self.w = int(CELL_W * self.scale)
        self.h = int(CELL_H * self.scale)
        # frames per zoom level, built with Pillow (GDI StretchBlt destroys the
        # alpha channel of a 32bpp DIB, so it must not be used to zoom)
        self._scale_frames: dict[float, _LazyFrames] = {}
        self._body_cache: dict[float, dict[str, list[tuple[int, int, int, int]]]] = {}
        self.frames = self._frames_at(self.scale)
        self.durations: dict[str, list[int]] = {
            n: list(d) for n, (_r, _c, d) in ALL_STATES.items()}

        self.x = 300.0
        self.y = float(self._default_y())
        self.paused = False
        self.dragging = False
        self.drag_moved = False
        self.drag_off = (0, 0)
        self.press_pt = (0, 0)
        self.pending_click: float | None = None
        self.mode = "idle"
        self.frame_i = 0
        self.frame_at = time.perf_counter()
        self.speed = 0.0
        self.target_x: float | None = None
        self.walking = False
        self.queue: list[str] = []
        # state machine:  pause -> rest(玩手机/干活中, 30s) -> travel(跑到另一侧) -> rest ...
        self.phase = "pause"
        self.phase_until = time.perf_counter() + random.uniform(1.5, 3.0)
        self.step_until = 0.0
        self.anim_factor = STROLL_FACTOR
        self.last_travel_dir = 0
        # --- interaction state ---
        self.pending_dblclick = False
        self.drag_times: list[float] = []
        self.hover_started: float | None = None
        self.petted_recently = False
        # --- drag / throw physics ---
        self.drag_target: tuple[float, float] | None = None
        self._drag_track: list[tuple[float, float, float]] = []
        self.vx = 0.0
        self.vy = 0.0
        self.flying = False
        # --- border knock-back while dragging ---
        self.knock = [0.0, 0.0]            # 视觉偏移（画面上朝屏幕内的位移）
        self.knock_vel = [0.0, 0.0]
        self.knock_at = [0.0, 0.0]         # 每面墙各自的触发时刻（做冷却）
        self.wall_push = [0.0, 0.0]        # 上次触发时挤进墙的深度（再深 30px 才补一次）
        self.on_wall = [False, False]      # 是否正贴着某面墙（用于只在"撞上瞬间"记日志）
        # --- topmost watchdog ---
        self._topmost_checked_at = 0.0
        self._topmost_asserted_at = time.perf_counter()
        self._create_window()
        logging.info("display scale %.2f -> pet scale %.2f (%dx%d)",
                     self.dpi, self.scale, self.w, self.h)

    # ------------------------------------------------------------ content
    def _render_state(self, name: str, scale: float) -> list[bytes]:
        """Render one animation at one zoom level (Pillow keeps alpha intact)."""
        if name in EXTRA_STATES:
            row, count, _durs = EXTRA_STATES[name]
            sheet = _sheet(SHEET2)
        else:
            row, count, _durs = STATES[name]
            sheet = _sheet()
        size = (int(CELL_W * scale), int(CELL_H * scale))
        frames = []
        for c in range(count):
            cell = sheet.crop((c * CELL_W, row * CELL_H, (c + 1) * CELL_W, (row + 1) * CELL_H))
            if scale != 1.0:
                cell = cell.resize(size, Image.LANCZOS)
            frames.append(premultiplied_bgra(cell))
        return frames

    def _frames_at(self, scale: float) -> _LazyFrames:
        """Frames of a zoom level (built lazily, kept for a few levels only)."""
        got = self._scale_frames.get(scale)
        if got is None:
            got = _LazyFrames(self, scale)
            self._scale_frames[scale] = got
            self._drop_stale_levels(scale)
        got.last_used = time.perf_counter()
        return got

    def _drop_stale_levels(self, keep: float) -> None:
        """Keep the cached levels bounded; each one holds a full set of frames."""
        while len(self._scale_frames) > MAX_CACHED_LEVELS:
            candidates = [s for s in self._scale_frames if s not in (keep, self.scale)]
            if not candidates:
                return
            oldest = min(candidates, key=lambda s: self._scale_frames[s].last_used)
            dropped = self._scale_frames.pop(oldest)
            logging.info("dropped zoom %.2f from cache (%d KB)", oldest,
                         dropped.cached_bytes() // 1024)
    def _preload_scales(self) -> None:
        """Warm the neighbouring zoom levels so 放大/缩小 never stalls.

        Only the current level plus its two neighbours are kept: each cached level costs
        one full set of frames, and on a scaled display the bigger ones are not cheap.
        """
        i = SCALES.index(self.rel_scale) if self.rel_scale in SCALES else 1
        wanted = [self.scale]
        for j in (i + 1, i - 1):
            if 0 <= j < len(SCALES):
                wanted.append(SCALES[j] * self.dpi)
        for s in wanted:
            levels = self._frames_at(s)
            for name in ALL_STATES:
                try:
                    levels[name]
                except Exception:
                    logging.warning("preload %.2f/%s failed", s, name, exc_info=True)
        logging.info("zoom levels ready: %s", [round(k, 2) for k in sorted(self._scale_frames)])

    def _body_boxes(self, scale: float, name: str) -> list[tuple[int, int, int, int]]:
        """Body box (x0, y0, x1, y1) of every frame of one animation.

        The window is a good deal bigger than the pose, so hit-testing the window finds
        the cursor on empty pixels - the greeting used to fire when the cursor only
        brushed the transparent corner around her.
        """
        per_scale = self._body_cache.setdefault(scale, {})
        got = per_scale.get(name)
        if got is None:
            w, h = int(CELL_W * scale), int(CELL_H * scale)
            boxes = []
            for f in self._frames_at(scale)[name]:
                alpha = np.frombuffer(f, dtype=np.uint8)[3::4].reshape(h, w)
                ys, xs = np.nonzero(alpha >= BODY_ALPHA_MIN)
                boxes.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                             if len(ys) else (0, 0, w, h))
            per_scale[name] = boxes
            got = boxes
        return got

    def _on_her(self, pt) -> bool:
        """True when the cursor is on the pose she is currently showing."""
        boxes = self._body_boxes(self.scale, self.mode)
        if not boxes:
            return False
        x0, y0, x1, y1 = boxes[self.frame_i % len(boxes)]
        return (self.x + x0 <= pt.x < self.x + x1) and (self.y + y0 <= pt.y < self.y + y1)

    # ------------------------------------------------------------ helpers
    def _work_area(self) -> tuple[int, int, int, int]:
        r = wintypes.RECT()
        u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom

    def _default_y(self) -> int:
        _, _, _, bottom = self._work_area()
        return max(0, bottom - self.h - 8)

    def _make_dib(self) -> None:
        screen_dc = u32.GetDC(None)
        self.mem_dc = g32.CreateCompatibleDC(screen_dc)
        u32.ReleaseDC(None, screen_dc)
        # one DIB, already at the on-screen (possibly zoomed) size
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = self.w
        bi.bmiHeader.biHeight = -self.h            # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        self.dib = g32.CreateDIBSection(self.mem_dc, ctypes.byref(bi), 0,
                                        ctypes.byref(bits), None, 0)
        if not self.dib or not bits.value:
            raise SystemExit(f"CreateDIBSection failed err={ctypes.get_last_error()}")
        self.bits = bits.value
        self._mem_old = g32.SelectObject(self.mem_dc, self.dib)

    # ------------------------------------------------------------ window
    def _create_window(self) -> None:
        hinst = k32.GetModuleHandleW(None)

        def handler(hwnd, msg, wparam, lparam):
            if msg == WM_LBUTTONDOWN:
                self.dragging = True
                self.drag_moved = False
                self.pending_dblclick = False
                pt = POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                self.drag_off = (pt.x - int(round(self.x)), pt.y - int(round(self.y)))
                self.press_pt = (pt.x, pt.y)
                self.drag_target = (self.x, self.y)
                self._drag_track = []
                u32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE and self.dragging:
                pt = POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                if not self.drag_moved and (abs(pt.x - self.press_pt[0]) > 4
                                            or abs(pt.y - self.press_pt[1]) > 4):
                    self.drag_moved = True
                    self.pending_dblclick = False      # she is being dragged, not tapped
                    self.queue = []
                    self.walking = False
                    self.target_x = None
                    self.set_mode("idle")
                if self.drag_moved:
                    # 只记目标，真正的移动交给 step() 里的弹簧跟手
                    self.drag_target = (float(pt.x - self.drag_off[0]),
                                        float(pt.y - self.drag_off[1]))
                return 0
            if msg == WM_LBUTTONDBLCLK:
                # a real double click arrives as DOWN, UP, DBLCLK, UP - the DBLCLK
                # stands in for the second press, so re-arm the press state here and
                # let the following UP decide: no movement = 抚摸, movement = a drag
                self.dragging = True
                self.drag_moved = False
                self.pending_click = None
                pt = POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                self.drag_off = (pt.x - int(round(self.x)), pt.y - int(round(self.y)))
                self.press_pt = (pt.x, pt.y)
                self.pending_dblclick = True
                u32.SetCapture(hwnd)
                return 0
            if msg == WM_LBUTTONUP and self.dragging:
                self.dragging = False
                self.drag_target = None
                u32.ReleaseCapture()
                self._touch()
                pt = POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                moved = self.drag_moved or (abs(pt.x - self.press_pt[0]) > 4
                                            or abs(pt.y - self.press_pt[1]) > 4)
                if moved:
                    self.pending_dblclick = False
                    self.drag_times.append(time.perf_counter())
                    recent = time.perf_counter() - DRAG_WINDOW
                    self.drag_times = [t for t in self.drag_times if t >= recent]
                    logging.info("drag #%d within %.0fs", len(self.drag_times), DRAG_WINDOW)
                    if len(self.drag_times) >= DRAG_LIMIT:
                        self.drag_times.clear()
                        logging.info("moved around %d times -> 沮丧", DRAG_LIMIT)
                        self.play(GRUMPY_REACTION)
                    else:
                        self.queue = []
                        self.walking = False
                        self.target_x = None
                        self.set_mode("idle")
                        self.phase = "pause"
                        self.phase_until = time.perf_counter() + random.uniform(1.0, 2.5)
                        left, top, right, bottom = self._work_area()
                        inside = (left <= self.x <= right - self.w) and (top <= self.y <= bottom - self.h)
                        vx, vy = self._release_velocity()
                        speed = math.hypot(vx, vy)
                        if not inside:
                            # 真被放到了屏幕外（多显示器变化等情况）：给个朝屏内的初速度弹回来
                            vx = (left - self.x if self.x < left else (right - self.w) - self.x) or 260.0
                            vy = -180.0
                            speed = max(speed, THROW_MIN_SPEED)
                            logging.info("released outside the work area -> bounce back in")
                        if speed >= THROW_MIN_SPEED:
                            clamp = lambda v: max(-MAX_THROW_SPEED, min(MAX_THROW_SPEED, v))
                            self.vx, self.vy = clamp(vx), clamp(vy)
                            self.flying = True
                            self.paused = False
                            logging.info("throw at %.0f px/s (vx=%.0f vy=%.0f)", speed, self.vx, self.vy)
                elif self.pending_dblclick:
                    self.pending_dblclick = False
                    logging.info("double click -> 抚摸 (高兴跳)")
                    self.play(random.choice(DBLCLICK_REACTIONS))
                else:
                    self.pending_click = time.perf_counter()   # see if a double click follows
                return 0
            if msg == WM_RBUTTONUP:
                self._show_menu(hwnd)
                return 0
            if msg == WM_COMMAND:
                self._on_command(int(wparam) & 0xFFFF)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(handler)          # keep a reference alive
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = CS_DBLCLKS            # needed to receive WM_LBUTTONDBLCLK
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = u32.LoadCursorW(None, ctypes.c_wchar_p(32512))
        wc.lpszClassName = "MimoPetWindow"
        if not u32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):
                logging.warning("RegisterClassExW err=%s", err)

        left, top, right, bottom = self._work_area()
        self.x = min(max(self.x, left), right - self.w)
        self.y = min(max(self.y, top), bottom - self.h)

        self.hwnd = u32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "MimoPetWindow", "MimoPet", WS_POPUP | WS_VISIBLE,
            int(self.x), int(self.y), self.w, self.h, None, None, hinst, None)
        if not self.hwnd:
            raise SystemExit(f"CreateWindowExW failed err={ctypes.get_last_error()}")

        # a layered window must be shown explicitly, otherwise it stays invisible
        u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self._set_icon()
        logging.info("window hwnd=%s at (%d,%d) size %dx%d visible=%s",
                     self.hwnd, int(self.x), int(self.y), self.w, self.h,
                     bool(u32.IsWindowVisible(self.hwnd)))

        self._make_dib()
        self.blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self._blit(self.frames["idle"][0])

    def _set_icon(self) -> None:
        """Give the window the Mimo icon (shown by Alt-Tab and task switchers)."""
        if not ICON.is_file():
            return
        try:
            hicon = u32.LoadImageW(None, str(ICON), IMAGE_ICON, 0, 0,
                                   LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if hicon:
                u32.SendMessageW(self.hwnd, WM_SETICON, ICON_BIG, hicon)
                u32.SendMessageW(self.hwnd, WM_SETICON, ICON_SMALL, hicon)
                logging.info("icon set from %s", ICON)
        except Exception:
            logging.warning("icon load failed", exc_info=True)

    def _build_menu(self):
        """The right-click menu (built separately from showing it, so it can be inspected)."""
        menu = u32.CreatePopupMenu()
        u32.AppendMenuW(menu, 0, 5, "投喂")
        u32.AppendMenuW(menu, 0, 6, "抚摸")
        u32.AppendMenuW(menu, 0x800, 0, None)
        u32.AppendMenuW(menu, 0, 1, "继续" if self.paused else "暂停")
        si = SCALES.index(self.rel_scale) if self.rel_scale in SCALES else 1
        u32.AppendMenuW(menu, 0 if si < len(SCALES) - 1 else MF_ZOOM_OFF, 2, "放大")
        u32.AppendMenuW(menu, 0 if si > 0 else MF_ZOOM_OFF, 3, "缩小")
        u32.AppendMenuW(menu, 0x800, 0, None)
        sub = u32.CreatePopupMenu()
        for i, state in enumerate(ACTION_MENU):
            u32.AppendMenuW(sub, 0, CMD_ACTION + i, STATE_LABELS[state])
        u32.AppendMenuW(sub, 0x800, 0, None)
        u32.AppendMenuW(sub, 0, CMD_ACTION + len(ACTION_MENU), "走一段")
        u32.AppendMenuW(menu, MF_POPUP, sub, "动作")
        u32.AppendMenuW(menu, 0x800, 0, None)
        u32.AppendMenuW(menu, 0, 4, "退出")
        return menu

    def _show_menu(self, hwnd) -> None:
        menu = self._build_menu()
        pt = POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(hwnd)
        cmd = u32.TrackPopupMenu(menu, TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
        u32.DestroyMenu(menu)
        if cmd:
            self._on_command(cmd)

    def _on_command(self, cmd: int) -> None:
        if cmd == 1:
            self.paused = not self.paused
        elif cmd in (2, 3):
            i = SCALES.index(self.rel_scale) if self.rel_scale in SCALES else 1
            i = min(i + 1, len(SCALES) - 1) if cmd == 2 else max(i - 1, 0)
            self._rescale(SCALES[i])
        elif cmd >= CMD_ACTION:
            k = cmd - CMD_ACTION
            self._touch()
            if k < len(ACTION_MENU):
                state = ACTION_MENU[k]
                logging.info("menu 动作 -> %s", state)
                self.play([state])
            else:
                logging.info("menu 动作 -> 走一段")
                self.start_travel()
        elif cmd == 4:
            u32.PostQuitMessage(0)
        elif cmd == 5:
            self.feed()
        elif cmd == 6:
            self.stroke()

    def _rescale(self, rel: float) -> None:
        scale = rel * self.dpi
        if scale == self.scale:
            return
        cx, cy = self.x + self.w / 2, self.y + self.h
        self.rel_scale = rel
        self.scale = scale
        self.w, self.h = int(CELL_W * scale), int(CELL_H * scale)
        self.frames = self._frames_at(scale)          # cached: no re-render
        old_dc, old_dib, old_sel = self.mem_dc, self.dib, self._mem_old
        self._make_dib()
        g32.SelectObject(old_dc, old_sel)
        g32.DeleteObject(old_dib)
        g32.DeleteDC(old_dc)
        self.x, self.y = cx - self.w / 2, cy - self.h
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(self.x), int(self.y),
                         self.w, self.h, SWP_NOACTIVATE)
        self.frame_i = 0
        logging.info("rescale -> %.2f (rel %.2f, %dx%d)", scale, rel, self.w, self.h)

    def _draw_xy(self) -> tuple[float, float]:
        """Where she is drawn: physical position plus the knock-back offset."""
        return self.x + self.knock[0], self.y + self.knock[1]

    def _move_window(self) -> None:
        dx, dy = self._draw_xy()
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(round(dx)), int(round(dy)),
                         self.w, self.h, SWP_NOACTIVATE)

    def _blit(self, frame: bytes) -> None:
        ctypes.memmove(self.bits, frame, len(frame))
        dx, dy = self._draw_xy()
        pt_dst, size, pt_src = POINT(int(round(dx)), int(round(dy))), SIZE(self.w, self.h), POINT(0, 0)
        u32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size),
                                self.mem_dc, ctypes.byref(pt_src), 0,
                                ctypes.byref(self.blend), ULW_ALPHA)

    # ------------------------------------------------------------ drag, throw, topmost
    def _track_drag(self, now: float) -> None:
        """Remember the last ~0.12s of motion so a release knows how fast it was."""
        self._drag_track.append((now, self.x, self.y))
        cutoff = now - 0.12
        while len(self._drag_track) > 2 and self._drag_track[0][0] < cutoff:
            self._drag_track.pop(0)

    def _release_velocity(self) -> tuple[float, float]:
        track, self._drag_track = self._drag_track, []
        if len(track) < 2:
            return 0.0, 0.0
        t0, x0, y0 = track[0]
        t1, x1, y1 = track[-1]
        dt = max(t1 - t0, 1e-3)
        return (x1 - x0) / dt, (y1 - y0) / dt

    def _clamp_inside(self, x: float, y: float) -> tuple[float, float]:
        """Keep the whole pet inside the work area (it can no longer be dragged off-screen)."""
        left, top, right, bottom = self._work_area()
        return min(max(x, float(left)), float(right - self.w)), min(max(y, float(top)), float(bottom - self.h))

    def _hit_border(self, axis: int, overshoot: float, now: float) -> None:
        """Kick away from the wall the cursor is being pressed into.

        ``overshoot`` is how far past the wall the cursor wants to go, signed, so
        negating it always points back towards the screen: past the right/bottom wall
        it kicks left/up, past the left/top wall it kicks right/down.

        Fired once per contact (plus one more if she is pushed 30px deeper afterwards),
        so holding her against the edge settles instead of jittering in place.
        """
        if now - self.knock_at[axis] < 0.18:
            return
        self.knock_at[axis] = now
        self.knock_vel[axis] = -max(min(overshoot, 40.0), -40.0) * KNOCK_KICK

    def _step_knock(self, dt: float) -> None:
        """Run the knock-back spring and clear it once it has settled."""
        for axis in (0, 1):
            if self.knock[axis] == 0.0 and self.knock_vel[axis] == 0.0:
                continue
            accel = -KNOCK_STIFF * self.knock[axis] - KNOCK_DAMP * self.knock_vel[axis]
            self.knock_vel[axis] += accel * dt
            self.knock[axis] += self.knock_vel[axis] * dt
            if abs(self.knock[axis]) < 0.3 and abs(self.knock_vel[axis]) < 8.0:
                self.knock[axis] = 0.0
                self.knock_vel[axis] = 0.0

    def _follow_cursor(self, dt: float, now: float) -> None:
        """Over-damped spring towards the cursor: catches up without jitter or overshoot.

        The spring aims at the cursor but the position is clamped to the work area, so
        the pet can be pushed right up to an edge and never past it; squeezing into an
        edge charges a knock-back that bounces it back towards the screen.
        """
        if self.drag_target is None:
            return
        tx, ty = self.drag_target
        k = 1.0 - math.exp(-dt / DRAG_FOLLOW_TAU)
        raw_x = self.x + (tx - self.x) * k
        raw_y = self.y + (ty - self.y) * k
        left, top, right, bottom = self._work_area()
        max_x, max_y = float(right - self.w), float(bottom - self.h)
        # signed overshoot per axis: positive = past the right/bottom wall
        over = [0.0, 0.0]
        if raw_x < left:
            over[0] = raw_x - left
        elif raw_x > max_x:
            over[0] = raw_x - max_x
        if raw_y < top:
            over[1] = raw_y - top
        elif raw_y > max_y:
            over[1] = raw_y - max_y
        hit = (over[0] != 0.0, over[1] != 0.0)
        for axis, name in ((0, "horizontal"), (1, "vertical")):
            if hit[axis] and not self.on_wall[axis]:
                logging.info("pushed against the %s border", name)
            if hit[axis]:
                # first contact, or pushed 30px deeper: kick again
                if (not self.on_wall[axis] or abs(over[axis]) > self.wall_push[axis] + 30.0):
                    self._hit_border(axis, over[axis], now)
                    self.wall_push[axis] = abs(over[axis])
            else:
                self.wall_push[axis] = 0.0
        self.on_wall[0], self.on_wall[1] = hit
        self.x, self.y = min(max(raw_x, left), max_x), min(max(raw_y, top), max_y)
        self._track_drag(now)
        self._move_window()

    def _fly(self, dt: float, now: float) -> None:
        """Parabolic flight after a throw: gravity, wall bounce, ground friction."""
        left, top, right, bottom = self._work_area()
        drag = AIR_DRAG ** dt
        self.vy += GRAVITY * dt
        self.vx *= drag
        self.vy *= drag
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.x < left:
            self.x, self.vx = float(left), -self.vx * BOUNCE_WALL
        elif self.x > right - self.w:
            self.x, self.vx = float(right - self.w), -self.vx * BOUNCE_WALL
        if self.y < top:
            self.y, self.vy = float(top), -self.vy * BOUNCE_WALL
        ground = float(bottom - self.h)
        on_ground = self.y >= ground
        if on_ground:
            self.y = ground
            if self.vy > 0:
                impact = self.vy
                self.vy = -self.vy * BOUNCE_GROUND
                if abs(self.vy) < SETTLE_SPEED:
                    self.vy = 0.0
                elif impact > 400:
                    logging.info("landed at %.0f px/s", impact)
            self.vx *= GROUND_FRICTION ** (dt * 60.0)
        if on_ground and self.vy == 0.0 and abs(self.vx) < SETTLE_SPEED:
            self.flying = False
            self.vx = self.vy = 0.0
            if self.phase != "interact":
                self.phase = "pause"
                self.phase_until = now + random.uniform(0.8, 2.0)
            logging.info("throw settled at x=%.0f y=%.0f", self.x, self.y)
        if abs(self.vx) > FLY_RUN_SPEED:
            self.set_mode("running-right" if self.vx > 0 else "running-left")
            self.anim_factor = DASH_FACTOR
        elif self.mode not in ("jumping",):
            self.set_mode("jumping")
            self.anim_factor = STROLL_FACTOR
        self._move_window()

    def _watch_topmost(self, now: float) -> None:
        """Re-assert the topmost band now and then.

        Setting WS_EX_TOPMOST is not enough to stay above other windows: only a
        SetWindowPos(HWND_TOPMOST) call actually puts the window in the topmost band, so
        anything that disturbs the z-order (a fullscreen app, another always-on-top
        window) can leave her buried while the style bit still looks right.
        """
        if now - self._topmost_checked_at < TOPMOST_TICK:
            return
        self._topmost_checked_at = now
        if now - self._topmost_asserted_at < TOPMOST_REASSERT:
            return
        self._topmost_asserted_at = now
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # ------------------------------------------------------------ behaviour
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.frame_i = 0
        self.frame_at = time.perf_counter()

    # ---- state machine: pause -> rest(玩手机/干活中 30s) -> travel(跑到另一侧) -> rest ...
    def start_rest(self) -> None:
        self.queue = []
        self.walking = False
        self.target_x = None
        self.step_until = 0.0
        self.anim_factor = STROLL_FACTOR
        self.set_mode(random.choice(REST_STATES))
        self.phase = "rest"
        self.phase_until = time.perf_counter() + REST_SECONDS
        frame_ms = self.durations[self.mode][0] * (
            WAIT_FACTOR if self.mode in REST_STATES else self.anim_factor)
        logging.info("rest -> %s for %.0fs (%.0f ms/帧)", self.mode, REST_SECONDS, frame_ms)

    def start_travel(self) -> None:
        """Set off for a random distance in a random direction, staying on the
        current horizontal line (only x changes). No need to cross the screen."""
        left, _, right, _ = self._work_area()
        d = random.choice([-1, 1])
        reach = random.uniform(TRAVEL_MIN, TRAVEL_MAX) * self.scale
        target = self.x + d * reach
        if target < left or target > right - self.w:       # no room that way: turn around
            d = -d
            target = self.x + d * reach
        self.target_x = min(max(target, left), right - self.w)
        self.set_mode("running-right" if self.target_x > self.x else "running-left")
        self.speed = DASH_SPEED * self.scale
        self.anim_factor = DASH_FACTOR
        self.walking = abs(self.target_x - self.x) > 3.0
        self.phase = "travel"
        self.phase_until = time.perf_counter() + 120.0        # safety cap
        logging.info("travel %s by %.0fpx to x=%.0f",
                     "right" if self.target_x > self.x else "left",
                     abs(self.target_x - self.x), self.target_x)

    def play(self, steps: list[str]) -> None:
        """A user-triggered reaction; when it finishes she goes back to resting."""
        self.queue = list(steps)
        self.walking = False
        self.target_x = None
        self.step_until = 0.0
        self.anim_factor = STROLL_FACTOR
        self.phase = "interact"
        self._next_step()

    def _next_step(self) -> None:
        if not self.queue:
            if self.phase == "interact":
                self.set_mode("idle")
                self.phase = "pause"
                self.phase_until = time.perf_counter() + 0.8
            return
        state = self.queue.pop(0)
        self.set_mode(state)
        self.step_until = (time.perf_counter()
                           + sum(self.durations[state]) * self.anim_factor / 1000.0)
        logging.info("step -> %s (remaining %s)", state, self.queue)

    def _touch(self) -> None:
        """The cursor is on her, or she was just handled: restart hover detection."""
        self.hover_started = None
        self.petted_recently = True

    def feed(self) -> None:
        logging.info("menu 投喂 -> 吃饭")
        self._touch()
        self.play(random.choice(CLICK_REACTIONS))

    def stroke(self) -> None:
        logging.info("menu 抚摸 -> 高兴跳")
        self._touch()
        self.play(random.choice(DBLCLICK_REACTIONS))

    def step(self, dt: float) -> None:
        now = time.perf_counter()
        self._watch_topmost(now)
        self._step_knock(dt)

        # a lone click only counts once we are sure no double click followed: 单击 -> 投喂
        if self.pending_click is not None and now - self.pending_click > CLICK_DELAY:
            self.pending_click = None
            logging.info("single click -> 投喂 (吃饭)")
            self.play(random.choice(CLICK_REACTIONS))

        # the cursor brushing over her without clicking is a greeting: 打招呼
        pt = POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        on_her = self._on_her(pt)
        if on_her and not self.dragging and not self.flying:
            if self.hover_started is None:
                self.hover_started = now
                logging.info("cursor ON her at (%d,%d); window x=%.0f y=%.0f w=%d h=%d",
                             pt.x, pt.y, self.x, self.y, self.w, self.h)
            elif now - self.hover_started > HOVER_PET_SECONDS and not self.petted_recently:
                self.hover_started = None
                self.petted_recently = True
                logging.info("cursor touched her -> 打招呼 (挥手)")
                self.play(random.choice(HOVER_REACTIONS))
        else:
            self.hover_started = None
            self.petted_recently = False

        if self.dragging:
            self._follow_cursor(dt, now)
            self._blit(self.frames["idle"][0])
            return

        if self.flying:
            # 被甩出去：物理优先，动画照常推进
            self._fly(dt, now)
            durs = self.durations[self.mode]
            if now - self.frame_at >= durs[self.frame_i] * self.anim_factor / 1000.0:
                self.frame_i = (self.frame_i + 1) % len(durs)
                self.frame_at = now
            frames = self.frames[self.mode]
            self._blit(frames[self.frame_i % len(frames)])
            return

        if self.paused:
            self._blit(self.frames["idle"][0])
            return

        # animation timing, slowed by anim_factor so the motion reads naturally
        durs = self.durations[self.mode]
        factor = WAIT_FACTOR if self.mode in REST_STATES else self.anim_factor
        if now - self.frame_at >= durs[self.frame_i] * factor / 1000.0:
            loop_done = self.frame_i == len(durs) - 1
            self.frame_i = (self.frame_i + 1) % len(durs)
            self.frame_at = now
            if self.mode in REST_STATES and loop_done:
                # one pass of a rest animation is done: stay on the first pose a while
                # （动完安静几秒——这三套动作"安静"就靠这里，而不是把帧拖慢）
                self.frame_at = now + random.uniform(WAIT_HOLD_MIN, WAIT_HOLD_MAX)

        if self.phase == "travel" and self.walking and self.target_x is not None:
            dx = self.target_x - self.x
            if abs(dx) < 3.0:
                self.walking = False
                self.target_x = None
                self.start_rest()
            else:
                self.x += math.copysign(min(abs(dx), self.speed * dt), dx)
                left, _, right, _ = self._work_area()
                self.x = min(max(self.x, left), right - self.w)
                self._move_window()
        elif self.phase == "interact" and self.step_until and now >= self.step_until:
            self.step_until = 0.0
            self._next_step()
        elif self.phase == "rest" and now >= self.phase_until:
            self.start_travel()
        elif self.phase == "pause" and now >= self.phase_until:
            self.start_rest()

        frames = self.frames[self.mode]
        self._blit(frames[self.frame_i % len(frames)])

    def run(self) -> None:
        # render the other zoom levels in the background right away
        threading.Thread(target=self._preload_scales, daemon=True).start()
        msg = MSG()
        last = time.perf_counter()
        logging.info("message loop start")
        while True:
            while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_QUIT:
                    logging.info("quit")
                    return
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
            now = time.perf_counter()
            self.step(min(now - last, 0.1))
            last = now
            time.sleep(1.0 / FPS)


if __name__ == "__main__":
    try:
        # single instance: a second copy would just fight the first one
        u32.FindWindowW.restype = wintypes.HWND
        u32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        if u32.FindWindowW("MimoPetWindow", "MimoPet"):
            logging.info("another MimoPet instance is already running; exiting")
            raise SystemExit(0)
        _clean_stale_runtime_dirs()
        Pet().run()
    except SystemExit:
        pass
    except Exception:
        logging.exception("fatal")
        raise