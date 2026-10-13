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
import sys
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
    "jumping":       (4, 5, [140, 140, 140, 140, 280]),
    "failed":        (5, 8, [140] * 7 + [240]),
    "waiting":       (6, 6, [150] * 5 + [260]),
    "running":       (7, 6, [120] * 5 + [220]),
    "review":        (8, 6, [150] * 5 + [280]),
}

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
STROLL_SPEED = 78.0         # px per second while strolling (matches the run cycle)
DASH_SPEED = 200.0          # px per second while crossing the screen
REST_SECONDS = 30.0         # how long she stays on 玩手机 / 干活中 between trips
TRAVEL_MIN = 220.0          # px - shortest stroll between rests
TRAVEL_MAX = 620.0          # px - longest stroll (does not need to cross the screen)

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

# ---------------------------------------------------------------- win32 plumbing
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32

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
SWP_NOACTIVATE = 0x0010
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


class Pet:
    def __init__(self, scale: float = DEFAULT_SCALE) -> None:
        if not SHEET.is_file():
            raise SystemExit(f"missing spritesheet: {SHEET}")
        self.scale = scale
        self.frames: dict[str, list[bytes]] = {}
        self.durations: dict[str, list[int]] = {}
        self.w = int(CELL_W * scale)
        self.h = int(CELL_H * scale)
        self._load()

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
        self._create_window()

    # ------------------------------------------------------------ content
    def _load(self) -> None:
        sheet = Image.open(SHEET).convert("RGBA")
        expected = (8 * CELL_W, 9 * CELL_H)
        if sheet.size != expected:
            logging.warning("unexpected atlas size %s (expected %s)", sheet.size, expected)
        for name, (row, count, durs) in STATES.items():
            frames = []
            for c in range(count):
                cell = sheet.crop((c * CELL_W, row * CELL_H, (c + 1) * CELL_W, (row + 1) * CELL_H))
                if self.scale != 1.0:
                    cell = cell.resize((int(CELL_W * self.scale), int(CELL_H * self.scale)),
                                       Image.LANCZOS)
                frames.append(premultiplied_bgra(cell))
            self.frames[name] = frames
            self.durations[name] = list(durs)
        logging.info("loaded %d states, cell %dx%d", len(self.frames), self.w, self.h)

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
        g32.SelectObject(self.mem_dc, self.dib)

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
                    self.x = float(pt.x - self.drag_off[0])
                    self.y = float(pt.y - self.drag_off[1])
                    self._move_window()
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

    def _show_menu(self, hwnd) -> None:
        menu = u32.CreatePopupMenu()
        u32.AppendMenuW(menu, 0, 5, "投喂")
        u32.AppendMenuW(menu, 0, 6, "抚摸")
        u32.AppendMenuW(menu, 0x800, 0, None)
        u32.AppendMenuW(menu, 0, 1, "继续" if self.paused else "暂停")
        u32.AppendMenuW(menu, 0, 2, "放大")
        u32.AppendMenuW(menu, 0, 3, "缩小")
        u32.AppendMenuW(menu, 0x800, 0, None)
        u32.AppendMenuW(menu, 0, 4, "退出")
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
            i = SCALES.index(self.scale) if self.scale in SCALES else 1
            i = min(i + 1, len(SCALES) - 1) if cmd == 2 else max(i - 1, 0)
            self._rescale(SCALES[i])
        elif cmd == 4:
            u32.PostQuitMessage(0)
        elif cmd == 5:
            self.feed()
        elif cmd == 6:
            self.stroke()

    def _rescale(self, scale: float) -> None:
        cx, cy = self.x + self.w / 2, self.y + self.h
        self.scale = scale
        self.w, self.h = int(CELL_W * scale), int(CELL_H * scale)
        self._load()
        g32.DeleteObject(self.dib)
        g32.DeleteDC(self.mem_dc)
        self._make_dib()
        self.x, self.y = cx - self.w / 2, cy - self.h
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(self.x), int(self.y),
                         self.w, self.h, SWP_NOACTIVATE)
        self.frame_i = 0

    def _move_window(self) -> None:
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(round(self.x)), int(round(self.y)),
                         self.w, self.h, SWP_NOACTIVATE)

    def _blit(self, frame: bytes) -> None:
        ctypes.memmove(self.bits, frame, len(frame))
        pt_dst, size, pt_src = POINT(int(round(self.x)), int(round(self.y))), SIZE(self.w, self.h), POINT(0, 0)
        u32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size),
                                self.mem_dc, ctypes.byref(pt_src), 0,
                                ctypes.byref(self.blend), ULW_ALPHA)

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
        self.set_mode(random.choice(["waiting", "running"]))
        self.phase = "rest"
        self.phase_until = time.perf_counter() + REST_SECONDS
        logging.info("rest -> %s for %.0fs", self.mode, REST_SECONDS)

    def start_travel(self) -> None:
        """Set off for a random distance in a random direction, staying on the
        current horizontal line (only x changes). No need to cross the screen."""
        left, _, right, _ = self._work_area()
        d = random.choice([-1, 1])
        reach = random.uniform(TRAVEL_MIN, TRAVEL_MAX)
        target = self.x + d * reach
        if target < left or target > right - self.w:       # no room that way: turn around
            d = -d
            target = self.x + d * reach
        self.target_x = min(max(target, left), right - self.w)
        self.set_mode("running-right" if self.target_x > self.x else "running-left")
        self.speed = DASH_SPEED
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

        # a lone click only counts once we are sure no double click followed: 单击 -> 投喂
        if self.pending_click is not None and now - self.pending_click > CLICK_DELAY:
            self.pending_click = None
            logging.info("single click -> 投喂 (吃饭)")
            self.play(random.choice(CLICK_REACTIONS))

        # the cursor brushing over her without clicking is a greeting: 打招呼
        pt = POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        on_her = (self.x <= pt.x < self.x + self.w) and (self.y <= pt.y < self.y + self.h)
        if on_her and not self.dragging:
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

        if self.paused or self.dragging:
            self._blit(self.frames["idle"][0])
            return

        # animation timing, slowed by anim_factor so the motion reads naturally
        durs = self.durations[self.mode]
        if now - self.frame_at >= durs[self.frame_i] * self.anim_factor / 1000.0:
            self.frame_i = (self.frame_i + 1) % len(durs)
            self.frame_at = now

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
        Pet().run()
    except SystemExit:
        pass
    except Exception:
        logging.exception("fatal")
        raise