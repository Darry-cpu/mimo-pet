# -*- coding: utf-8 -*-
"""A small always-on-top bubble window — the pet's only way of showing text.

The bubble artwork is the hand-drawn cloud in ``assets/speech-bubble.png``: a black
outline with a soft grey shadow whose interior was filled white (see the notes in
the repo for how that was derived — the enclosed region is found by flooding the
"outside" up to the dark outline).

Nothing in here talks to the network or the mailbox; it just shows a line of text
for a while and calls back when clicked.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY, WM_LBUTTONUP, WM_NCHITTEST, WM_ERASEBKGND = 0x0002, 0x0202, 0x0084, 0x0014

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = (
    0x00080000, 0x00000008, 0x00000080, 0x08000000)
SW_SHOWNOACTIVATE, SW_HIDE = 4, 0
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0010, 0x0001, 0x0040
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x00000002, 0, 1
SPI_GETWORKAREA = 0x0030

FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
                   r"C:\Windows\Fonts\msyhbd.ttc",
                   r"C:\Windows\Fonts\simhei.ttf")     # 黑体

# --- 气泡素材的几何（占整图比例），由 pet-run 的处理脚本算出来 ---
BUBBLE_NAME = "speech-bubble.png"
SAFE_RECT = (0.1963, 0.1621, 0.8492, 0.5488)   # 文字安全区（云朵内的最大矩形）
TIP_FRAC = (0.8709, 0.9116)                    # 最小圆点中心：气泡"指向"她的位置
WIDTH_FACTOR = 0.85                            # 气泡宽度 = 桌宠宽度的 0.85 倍
FONT_PT = 13                                   # 逻辑像素；塞不下会自动往小缩
BOTTOM_FRAC = TIP_FRAC[1]                      # 圆点中心以下的余量（投影/圆点下缘）
GAP = 8                                        # 气泡底边与她头顶之间留多少像素
INK = (32, 32, 32, 255)

_art_cache: Image.Image | None = None


def _asset(name: str) -> Path:
    """和 mimo_pet._resource 一样的查找顺序：打包目录 / 脚本同目录 / 上一级 assets。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    here = Path(__file__).resolve().parent
    for cand in (here / name, here.parent / "assets" / name):
        if cand.is_file():
            return cand
    return here / name


def _art() -> Image.Image:
    global _art_cache
    if _art_cache is None:
        _art_cache = Image.open(_asset(BUBBLE_NAME)).convert("RGBA")
    return _art_cache


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


u32.RegisterClassExW.restype = wintypes.WORD
u32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEX),)
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
u32.DefWindowProcW.restype = LRESULT
u32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.UpdateLayeredWindow.restype = wintypes.BOOL
u32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.HDC, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD)
u32.GetDC.restype = wintypes.HDC
u32.GetDC.argtypes = (wintypes.HWND,)
u32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
u32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT)
u32.DestroyWindow.argtypes = (wintypes.HWND,)
g32.CreateCompatibleDC.restype = wintypes.HDC
g32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
g32.CreateDIBSection.restype = wintypes.HBITMAP
g32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
g32.SelectObject.restype = wintypes.HANDLE
g32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
g32.DeleteObject.argtypes = (wintypes.HANDLE,)
g32.DeleteDC.argtypes = (wintypes.HDC,)

# 这些符号不该出现在行首（中文避头规则），换行时把前一个字一起带下来
HEAD_FORBIDDEN = "，。！？；：、）】》」』”’…％‰℃·,.;:!?)]}>"


def premultiplied_bgra(img: Image.Image) -> bytes:
    """Raw bytes for a 32bpp top-down DIB (premultiplied, as UpdateLayeredWindow wants)."""
    rgba = img.convert("RGBA")
    data = bytearray(rgba.tobytes())
    for i in range(0, len(data), 4):
        a = data[i + 3]
        if a != 255:
            data[i] = data[i] * a // 255
            data[i + 1] = data[i + 1] * a // 255
            data[i + 2] = data[i + 2] * a // 255
    data[0::4], data[2::4] = data[2::4], data[0::4]
    return bytes(data)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Greedy wrap that also handles CJK (which has no spaces to break on)."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            probe = cur + ch
            if cur and draw.textlength(probe, font=font) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur = probe
        lines.append(cur)
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in HEAD_FORBIDDEN and len(lines[i - 1]) > 1:
            lines[i] = lines[i - 1][-1] + lines[i]
            lines[i - 1] = lines[i - 1][:-1]
    return lines


class SpeechBubble:
    """One reusable bubble window; `show()` replaces whatever is on screen."""

    def __init__(self, dpi: float = 1.0) -> None:
        self.dpi = max(1.0, float(dpi))
        self.hwnd: int | None = None
        self._proc = None
        self._mem_dc = None
        self._dib = None
        self._old_bitmap = None
        self._bits = 0
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self.w = 0
        self.h = 0
        self.pos = (0, 0)
        self.tip = (0, 0)
        self.tip_offset = (0, 0)
        self.until = 0.0
        self.on_click = None                   # callable() -> None

    # ---------------------------------------------------------------- window
    def _create(self) -> None:
        hinst = k32.GetModuleHandleW(None)

        def handler(hwnd, msg, wparam, lparam):
            if msg == WM_LBUTTONUP:
                if self.on_click is not None:
                    try:
                        self.on_click()
                    except Exception:                                   # noqa: BLE001
                        logging.warning("bubble click handler failed", exc_info=True)
                self.hide()
                return 0
            if msg == WM_NCHITTEST:
                return 1                       # HTCLIENT: stay clickable
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(handler)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = u32.LoadCursorW(None, ctypes.c_wchar_p(32649))     # hand
        wc.lpszClassName = "MimoPetBubble"
        if not u32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):           # 1410 = already registered
                logging.warning("bubble RegisterClassExW err=%s", err)
        self.hwnd = u32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "MimoPetBubble", "MimoPetBubble", WS_POPUP,
            0, 0, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            raise RuntimeError("bubble CreateWindowExW failed err=%s" % ctypes.get_last_error())

    def _ensure_dib(self, w: int, h: int) -> None:
        if self._dib and self.w == w and self.h == h:
            return
        self._free_dib()
        screen = u32.GetDC(None)
        self._mem_dc = g32.CreateCompatibleDC(screen)
        u32.ReleaseDC(None, screen)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h             # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        self._dib = g32.CreateDIBSection(self._mem_dc, ctypes.byref(bi), 0,
                                         ctypes.byref(bits), None, 0)
        if not self._dib or not bits.value:
            raise RuntimeError("bubble CreateDIBSection failed")
        self._bits = bits.value
        self._old_bitmap = g32.SelectObject(self._mem_dc, self._dib)
        self.w, self.h = w, h

    def _free_dib(self) -> None:
        if self._dib:
            g32.SelectObject(self._mem_dc, self._old_bitmap)
            g32.DeleteObject(self._dib)
            self._dib = None
        if self._mem_dc:
            g32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        self.w = self.h = 0

    # ----------------------------------------------------------------- image
    def _render(self, text: str, pet_w: int) -> tuple[Image.Image, tuple[int, int]]:
        """把文字排进云朵的安全区里；返回成品图和"小圆点"在图中的位置。"""
        art = _art()
        target_w = max(120, int(round(pet_w * WIDTH_FACTOR)))
        img = art.resize((target_w, max(1, round(art.height * target_w / art.width))), Image.LANCZOS)
        w, h = img.size
        bx0, by0 = int(SAFE_RECT[0] * w), int(SAFE_RECT[1] * h)
        bx1, by1 = int(SAFE_RECT[2] * w), int(SAFE_RECT[3] * h)
        box_w, box_h = max(10, bx1 - bx0), max(10, by1 - by0)

        probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        base = max(8, int(round(w * 0.056)))                  # 字号跟气泡尺寸联动
        font, lines, line_h = _font(base), [], 0
        for size in range(base, 6, -1):                       # 塞不下就逐级缩小字号
            font = _font(size)
            lines = _wrap(probe, text.strip(), font, box_w)
            line_h = int(round(size * 1.32))
            widest = max((probe.textlength(ln, font=font) for ln in lines), default=0)
            if widest <= box_w and line_h * len(lines) <= box_h:
                break

        dr = ImageDraw.Draw(img)
        y = by0 + max(0, (box_h - line_h * len(lines)) // 2)
        for ln in lines:
            x = bx0 + max(0, int((box_w - probe.textlength(ln, font=font)) / 2))
            dr.text((x, y), ln, font=font, fill=INK)
            y += line_h

        return img, (int(TIP_FRAC[0] * w), int(TIP_FRAC[1] * h))

    # ------------------------------------------------------------------- api
    def _tip_for(self, pet_x: float, pet_y: float, pet_w: int) -> tuple[int, int]:
        """把圆点中心放在她头顶正上方，让整个气泡（含圆点和投影）都在她上面，
        这样气泡和她不会互相遮盖。"""
        bottom_extra = (1.0 - BOTTOM_FRAC) * self.h          # 圆点中心往下的部分
        return (int(pet_x + pet_w * 0.42), int(pet_y - bottom_extra - GAP))

    def show(self, text: str, pet_x: float, pet_y: float, pet_w: int, pet_h: int = 0,
             seconds: float = 10.0, now: float | None = None) -> None:
        """Show `text` above the pet box (pet_x, pet_y, pet_w, pet_h)."""
        try:
            if self.hwnd is None:
                self._create()
            img, offset = self._render(text, pet_w)
            self.tip_offset = offset
            w, h = img.size
            self._ensure_dib(w, h)
            ctypes.memmove(self._bits, premultiplied_bgra(img), w * h * 4)
            self.tip = self._tip_for(pet_x, pet_y, pet_w)
            self.until = (now if now is not None else _now()) + seconds
            self._place()
            u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self._paint()
        except Exception:                                              # noqa: BLE001
            logging.warning("bubble show failed", exc_info=True)

    def tick(self, now: float, pet_x: float, pet_y: float, pet_w: int, pet_h: int) -> None:
        """Follow her while she walks, and hide once the timer is up."""
        if self.hwnd is None or not self.w:
            return
        if now >= self.until:
            self.hide()
            return
        tip = self._tip_for(pet_x, pet_y, pet_w)
        if tip != self.tip:
            self.tip = tip
            self._place()
            self._paint()

    def hide(self) -> None:
        if self.hwnd:
            u32.ShowWindow(self.hwnd, SW_HIDE)
        self.until = 0.0

    @property
    def visible(self) -> bool:
        return bool(self.hwnd) and bool(self.w) and self.until > _now()

    def close(self) -> None:
        if self.hwnd:
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._free_dib()

    # --------------------------------------------------------------- placing
    def _place(self) -> None:
        left, top, right, bottom = _work_area()
        ox, oy = self.tip_offset
        x = self.tip[0] - ox
        y = self.tip[1] - oy
        x = max(left + 4, min(x, right - self.w - 4))
        y = max(top + 4, min(y, bottom - self.h - 4))
        self.pos = (x, y)
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.w, self.h,
                         SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _paint(self) -> None:
        x, y = self.pos
        pt_dst = POINT(x, y)
        size = SIZE(self.w, self.h)
        pt_src = POINT(0, 0)
        u32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size),
                                self._mem_dc, ctypes.byref(pt_src), 0,
                                ctypes.byref(self._blend), ULW_ALPHA)


def _now() -> float:
    import time
    return time.perf_counter()


def _work_area() -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom
