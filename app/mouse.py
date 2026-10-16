# -*- coding: utf-8 -*-
"""罗技鼠标电量（HID++ 2.0），只读查询。

怎么读的：罗技的接收器（Unifying / Bolt）上有一个"厂商自定义"的 HID 接口，
用 HID++ 2.0 协议问它电池信息即可 —— 和 Linux 上 Solaar 的原理一样，只用 Windows 自带接口。

三个坑（都踩过，才写成现在这样）：
  1. **接收器路径不能写死**：换个 USB 口实例号就变，所以用 SetupAPI 枚举 HID 接口现找；
  2. **每条请求必须带递增的"会话号"**，只认会话号对得上的回复 —— 否则会一直读到上一条请求的回复，
     表现为"什么特征都支持""电量 70%/13%/7% 乱跳"；
  3. **设备休眠时会不回复**，所以读写都要带超时（异步 I/O），读不到就重试，绝不能把程序挂死。
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid.dll", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_FLAG_OVERLAPPED, OPEN_EXISTING = 0x40000000, 3
ERROR_IO_PENDING, WAIT_OBJECT_0 = 997, 0
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x2, 0x10
HID_MAX = 20

# HID++ 2.0 里电池相关的那几个特征号
FEAT_DEVICE_NAME = 0x0005
FEAT_BATTERY_STATUS = 0x1000        # 老设备：直接给百分比
FEAT_BATTERY_VOLTAGE = 0x1001       # 给电压（mV）
FEAT_UNIFIED_BATTERY = 0x1004       # 新设备（Lift 这类）：电量% + 档位 + 状态
FEATURES = (FEAT_UNIFIED_BATTERY, FEAT_BATTERY_VOLTAGE, FEAT_BATTERY_STATUS)

STATUS_TEXT = {0: "放电中", 1: "充电中", 2: "已充满", 3: "充电故障", 4: "未知"}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(ctypes.c_ulong))]


class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", ctypes.c_wchar * 512)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
k32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED))
k32.WriteFile.argtypes = k32.ReadFile.argtypes
k32.GetOverlappedResult.argtypes = (wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                    ctypes.POINTER(wintypes.DWORD), wintypes.BOOL)
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = (ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD)
setupapi.SetupDiEnumDeviceInterfaces.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
                                                 ctypes.POINTER(GUID), wintypes.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA))
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
setupapi.SetupDiDestroyDeviceInfoList.argtypes = (wintypes.HANDLE,)


def logitech_paths() -> list[str]:
    """枚举出罗技接收器上那些"厂商自定义"的 HID 接口路径（不写死实例号）。"""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    devs = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if devs in (None, 0, ctypes.c_void_p(-1).value):
        return []
    found = []
    try:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        i = 0
        while setupapi.SetupDiEnumDeviceInterfaces(devs, None, ctypes.byref(guid), i,
                                                   ctypes.byref(did)):
            i += 1
            need = wintypes.DWORD(0)
            detail = SP_DEVICE_INTERFACE_DETAIL_DATA()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 5
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(devs, ctypes.byref(did),
                                                             ctypes.byref(detail),
                                                             ctypes.sizeof(detail),
                                                             ctypes.byref(need), None):
                continue
            path = detail.DevicePath.lower()
            if "vid_046d" in path and "col0" in path:
                found.append(detail.DevicePath)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devs)
    # 厂商自定义的那一列（mi_02&col02）先试，能省掉一圈超时等待
    found.sort(key=lambda p: (0 if "mi_02&col02" in p else 1 if "mi_02" in p else 2, p))
    return found


class _Dev:
    def __init__(self, path: str, dev_index: int):
        self.sw = 0
        self.dev = dev_index
        self.h = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 3, None,
                                 OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        self.ok = self.h not in (None, 0, ctypes.c_void_p(-1).value)

    def close(self) -> None:
        if self.ok:
            k32.CloseHandle(self.h)
            self.ok = False

    def _io(self, fn, buf, size: int, timeout_ms: int):
        ov = OVERLAPPED()
        ov.hEvent = k32.CreateEventW(None, True, False, None)
        n = wintypes.DWORD(0)
        try:
            if not fn(self.h, buf, size, ctypes.byref(n), ctypes.byref(ov)):
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    return None
                if k32.WaitForSingleObject(ov.hEvent, timeout_ms) != WAIT_OBJECT_0:
                    k32.CancelIo(self.h)
                    return None
            if not k32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(n), False):
                return None
            return n.value
        finally:
            k32.CloseHandle(ov.hEvent)

    def _read(self, timeout_ms: int = 150):
        buf = (ctypes.c_ubyte * HID_MAX)()
        n = self._io(k32.ReadFile, buf, HID_MAX, timeout_ms)
        return list(buf[:n]) if n else None

    def _write(self, data: bytes) -> bool:
        buf = (ctypes.c_ubyte * HID_MAX)()
        for i, b in enumerate(data[:HID_MAX]):
            buf[i] = b
        return self._io(k32.WriteFile, buf, HID_MAX, 250) is not None

    def drain(self) -> None:
        while self._read(20) is not None:
            pass

    def call(self, feat: int, func: int, params: tuple = (), want_error: bool = False):
        self.sw = self.sw % 15 + 1
        sw = self.sw
        frame = bytes([0x11, self.dev, feat, ((func & 0x0F) << 4) | sw]) + bytes(params) + b"\x00" * 12
        for _ in range(2):
            self.drain()
            if not self._write(frame):
                time.sleep(0.05)
                continue
            deadline = time.time() + 0.5
            while time.time() < deadline:
                r = self._read(120)
                if not r or len(r) < 5:
                    continue
                if r[0] != 0x11 or r[1] != self.dev or r[3] != (((func & 0x0F) << 4) | sw):
                    continue                       # 不是这条请求的回复，丢掉
                if r[2] == 0xFF and not want_error:
                    return None
                return r
        return None

    def feature(self, feature_id: int):
        r = self.call(0x00, 0x0, (feature_id >> 8, feature_id & 0xFF), want_error=True)
        if r is None or r[2] == 0xFF or not r[4]:
            return None
        return r[4]

    def name(self) -> str:
        fi = self.feature(FEAT_DEVICE_NAME)
        if not fi:
            return ""
        c = self.call(fi, 0x0)
        if not c:
            return ""
        length = c[4]
        out = b""
        for i in range(0, max(1, (length + 15) // 16)):
            ch = self.call(fi, 0x1, (i,))
            if not ch:
                break
            out += bytes(ch[4:20])
        return out[:length].decode("utf-8", "replace").replace("\x00", "").strip()


_device_cache: tuple[str, int, str] | None = None       # (path, 设备号, 名字)


def _probe(path: str, dev_index: int):
    d = _Dev(path, dev_index)
    if not d.ok:
        return None
    try:
        for fid in FEATURES:
            fi = d.feature(fid)
            if fi is None:
                continue
            if fid == FEAT_UNIFIED_BATTERY:
                for _ in range(2):
                    r = d.call(fi, 0x1)
                    if r:
                        return path, dev_index, (r[4], STATUS_TEXT.get(r[6], "未知"), r[5], d.name())
            elif fid == FEAT_BATTERY_VOLTAGE:
                r = d.call(fi, 0x0)
                if r:
                    mv = (r[4] << 8) | r[5]
                    if mv:
                        pct = max(0, min(100, round((mv - 3300) / (4200 - 3300) * 100)))   # 粗估
                        return path, dev_index, (pct, "放电中", None, d.name())
            else:
                r = d.call(fi, 0x0)
                if r:
                    return path, dev_index, (r[4], STATUS_TEXT.get(r[6], "未知"), None, d.name())
    finally:
        d.close()
    return None


def read_battery(refresh: bool = False):
    """读鼠标电量。返回 dict(percent, status, level, name) 或 None（读不到）。"""
    global _device_cache
    if _device_cache and not refresh:
        path, idx, name = _device_cache
        try:
            d = _Dev(path, idx)
            if d.ok:
                try:
                    fi = d.feature(FEAT_UNIFIED_BATTERY)
                    if fi:
                        r = d.call(fi, 0x1)
                        if r:
                            return {"percent": r[4], "status": STATUS_TEXT.get(r[6], "未知"),
                                    "level": r[5], "name": name}
                    fi = d.feature(FEAT_BATTERY_STATUS)
                    if fi:
                        r = d.call(fi, 0x0)
                        if r:
                            return {"percent": r[4], "status": STATUS_TEXT.get(r[6], "未知"),
                                    "level": None, "name": name}
                finally:
                    d.close()
        except Exception:                                              # noqa: BLE001
            logging.debug("鼠标电量缓存路径失效，重新找", exc_info=True)
        _device_cache = None

    for path in logitech_paths():
        for dev_index in (0x02, 0x01, 0x03, 0x04, 0x05, 0x06, 0xFF):
            got = _probe(path, dev_index)
            if got:
                p, i, (pct, status, level, name) = got
                _device_cache = (p, i, name)
                logging.info("鼠标电量：%s 设备号0x%02X -> %d%% (%s)", name, i, pct, status)
                return {"percent": pct, "status": status, "level": level, "name": name}
    return None
