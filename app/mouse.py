# -*- coding: utf-8 -*-
"""鼠标电量（HID，只读查询）。

支持两批协议：
  - **罗技 HID++ 2.0**：接收器（Unifying / Bolt）上"厂商自定义"的 HID 接口，和 Solaar 一样；
  - **Nordic 52840**：一批国产游戏鼠标（ATK / VXE / VGN / Zaopin / Scyrox / 达尔优 / G-Wolves）
    用的贴牌芯片协议，17 字节问答式。协议细节见 `docs\国产游戏鼠标电量协议参考.md`。

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

# --- Nordic 52840（国产游戏鼠标贴牌芯片）---
# 协议：17 字节问答，报告 ID 0x08，子命令 0x04 = 电量查询。
# 校验和：17 字节全部相加 mod 256 == 0x55（请求里固定填 0x49 = 73）。
NORDIC_REPORT_ID = 0x08
NORDIC_SUB_BATTERY = 0x04
NORDIC_REPORT_LEN = 17
NORDIC_CHECKSUM_TARGET = 0x55

# (VID, 无线接收器 PID, 有线直连 PID, 显示名)
# 无线/有线是两个不同 PID；接收器出厂和鼠标配对，PID 随配对通道可能不同，
# 所以同一型号可能要多行（参考文档里 ATK Zero 就是两个接收器 PID）。
NORDIC52_MODELS = [
    (0x373B, 0x1031, 0x102E, "ATK F1 Ultimate"),
    (0x373B, 0x11D9, 0x11B6, "ATK A9 Ultimate"),
    (0x373B, 0x104D, 0x103F, "VXE MAD R"),
    (0x373B, 0x1040, 0x104C, "VXE MAD R Major Plus"),
    (0x3554, 0xF58A, 0xF58C, "VXE R1 Pro Max"),
    (0x3554, 0xF58E, 0xF58F, "VXE R1 SE+"),
    (0x3554, 0xF503, 0xF502, "VGN F1 Pro"),
    (0x3554, 0xFB3E, 0xFB3D, "VGN F2 Pro Max"),
    (0x3554, 0xF524, 0xF526, "Zaopin Z2 Mini"),
    (0x3554, 0xF5F7, 0xF5F6, "Scyrox V8"),
    (0x260D, 0x1175, 0x1193, "达尔优 A950 Air"),
    (0x260D, 0x1154, 0x1196, "达尔优 A950 Wing"),
    (0x33E4, 0x3854, 0x4719, "G-Wolves Lycan"),
]


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

# HidD_GetPreparsedData / HidP_GetCaps / HidD_FreePreparsedData —— 读用法页要用
hid.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
hid.HidD_GetPreparsedData.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p))
hid.HidP_GetCaps.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte * 0))  # 占位，实际用局部 HIDP_CAPS
hid.HidD_FreePreparsedData.argtypes = (ctypes.c_void_p,)
hid.HidD_GetAttributes.argtypes = (wintypes.HANDLE, ctypes.c_void_p)  # 同上，调用点用局部结构


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17)]


def _all_hid_paths() -> list[tuple[str, int, int, int, int]]:
    """枚举所有出现中的 HID 接口。

    返回 (设备路径, VID, PID, 用法页, 用法) 列表，按"先厂商自定义集合"排序。
    Nordic 芯片靠 (VID, PID + 用法页 0xFF02) 定位；罗技靠路径里的 vid_046d。
    """
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
            # 只读打开就能拿到 VID/PID/用法页，不用真读写
            h = k32.CreateFileW(detail.DevicePath, 0, 3, None, 3, 0x80, None)
            if h in (None, 0, ctypes.c_void_p(-1).value):
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    continue
                prep = ctypes.c_void_p()
                if not hid.HidD_GetPreparsedData(h, ctypes.byref(prep)):
                    continue
                try:
                    caps = HIDP_CAPS()
                    hid.HidP_GetCaps(prep, ctypes.byref(caps))
                finally:
                    hid.HidD_FreePreparsedData(prep)
                found.append((detail.DevicePath, attrs.VendorID, attrs.ProductID,
                              caps.UsagePage, caps.Usage))
            finally:
                k32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devs)
    # 厂商自定义集合（0xFF00~0xFFFF）优先，能省掉一圈超时等待
    found.sort(key=lambda r: (0 if r[3] >= 0xFF00 else 1, r[3], r[0]))
    return found


def _nordic_paths(vid: int, pid: int) -> list[str]:
    """Nordic 52840 那颗芯片的 HID 集合路径（用法页 0xFF02、用法 0x02）。"""
    return [path for path, v, p, up, us in _all_hid_paths()
            if v == vid and p == pid and up == 0xFF02 and us == 0x02]


def _nordic_battery(path: str) -> dict | None:
    """在 Nordic 52840 芯片上读一次电量。

    17 字节问答：报告 ID 0x08，子命令 0x04，校验和全部相加 mod 256 == 0x55。
    回复里偏移 6 = 电量百分比，偏移 7 = 有线标志（0=无线，1=插线）。
    """
    d = _Dev(path, 0)     # Nordic 不用 HID++ 那套设备号，占位填 0
    if not d.ok:
        return None
    try:
        frame = bytearray(NORDIC_REPORT_LEN)
        frame[0] = NORDIC_REPORT_ID
        frame[1] = NORDIC_SUB_BATTERY
        # 校验和：17 字节加起来 mod 256 必须等于 0x55，末字节回填
        frame[NORDIC_REPORT_LEN - 1] = (NORDIC_CHECKSUM_TARGET - sum(frame[:-1])) & 0xFF
        for _ in range(2):
            d.drain()
            if not d._write(bytes(frame)):
                time.sleep(0.05)
                continue
            deadline = time.time() + 0.5
            while time.time() < deadline:
                r = d._read(120)
                if not r or len(r) < NORDIC_REPORT_LEN:
                    continue
                if r[0] != NORDIC_REPORT_ID or r[1] != NORDIC_SUB_BATTERY:
                    continue                      # 不是这条请求的回复，丢掉
                if sum(r) & 0xFF != NORDIC_CHECKSUM_TARGET:
                    continue                      # 校验和不对，丢掉
                pct = r[6]
                wired = bool(r[7])
                if not 0 <= pct <= 100:
                    continue                      # 电量不可能超 100，丢掉
                if wired:
                    status = "已充满" if pct >= 100 else "充电中"
                else:
                    status = "放电中"
                return {"percent": pct, "status": status, "level": None,
                        "name": "Nordic 52840"}
    except Exception:                                              # noqa: BLE001
        logging.debug("Nordic 电量读取异常", exc_info=True)
    finally:
        d.close()
    return None


def _match_nordic(vid: int, pid: int) -> str | None:
    """VID/PID 能对上 Nordic 型号表就返回显示名，否则 None。"""
    for v, w, c, name in NORDIC52_MODELS:
        if v == vid and pid in (w, c):
            return name
    return None


_nordic_cache: tuple[str, str] | None = None       # (路径, 显示名)


def read_nordic(refresh: bool = False):
    """读 Nordic 52840 芯片鼠标的电量。找不到就返回 None。"""
    global _nordic_cache
    if _nordic_cache and not refresh:
        path, name = _nordic_cache
        try:
            got = _nordic_battery(path)
            if got:
                got["name"] = name
                return got
        except Exception:                                              # noqa: BLE001
            logging.debug("Nordic 缓存路径失效，重新找", exc_info=True)
        _nordic_cache = None

    for path, vid, pid, up, us in _all_hid_paths():
        name = _match_nordic(vid, pid)
        if not name:
            continue
        if up != 0xFF02 or us != 0x02:
            continue                           # 不在 Nordic 的目标集合上，跳过
        got = _nordic_battery(path)
        if got:
            _nordic_cache = (path, name)
            got["name"] = name
            logging.info("Nordic 鼠标电量：%s (VID:%04X PID:%04X) -> %d%% (%s)",
                         name, vid, pid, got["percent"], got["status"])
            return got
    return None


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
    """读鼠标电量。返回 dict(percent, status, level, name) 或 None（读不到）。

    先试罗技 HID++（接收器 / 有线 / 蓝牙），找不到再试 Nordic 52840 国产芯片。
    """
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

    # 罗技没找到，试 Nordic 52840 国产游戏鼠标芯片
    return read_nordic(refresh)
