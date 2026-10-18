from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from .models import InputEvent


WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_TIMER = 0x0113
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
RIDEV_PAGEONLY = 0x00000020
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
RI_KEY_BREAK = 0x0001


KEYBOARD_CONTROLS = {
    0x26: "up",
    0x28: "down",
    0x25: "left",
    0x27: "right",
    0x21: "page_up",
    0x22: "page_down",
    0x0D: "enter",
    0x1B: "escape",
}

KNOWN_CONSUMER_USAGES = {0x0041, 0x0224, 0x00E9, 0x00EA, 0x00CD, 0x00CF, 0x0223}


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class RID_DEVICE_INFO_HID(ctypes.Structure):
    _fields_ = [
        ("dwVendorId", wintypes.DWORD),
        ("dwProductId", wintypes.DWORD),
        ("dwVersionNumber", wintypes.DWORD),
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
    ]


class _RID_DEVICE_INFO_UNION(ctypes.Union):
    _fields_ = [("hid", RID_DEVICE_INFO_HID), ("reserved", wintypes.DWORD * 6)]


class RID_DEVICE_INFO(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("data", _RID_DEVICE_INFO_UNION),
    ]


@dataclass(frozen=True)
class RawInputDevice:
    path: str
    kind: str
    usage_page: int | None = None
    usage: int | None = None


def _device_info(handle: int) -> RID_DEVICE_INFO | None:
    info = RID_DEVICE_INFO()
    info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
    size = wintypes.UINT(info.cbSize)
    result = ctypes.windll.user32.GetRawInputDeviceInfoW(
        handle, RIDI_DEVICEINFO, ctypes.byref(info), ctypes.byref(size)
    )
    return info if result >= 0 else None


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Raw Input is only available on Windows")


def _device_name(handle: int) -> str:
    user32 = ctypes.windll.user32
    size = wintypes.UINT(0)
    if user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size)) < 0:
        return ""
    if not size.value:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value + 1)
    if user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(size)) < 0:
        return ""
    return buffer.value


def enumerate_devices() -> list[RawInputDevice]:
    _require_windows()
    user32 = ctypes.windll.user32
    count = wintypes.UINT(0)
    item_size = ctypes.sizeof(RAWINPUTDEVICELIST)
    if user32.GetRawInputDeviceList(None, ctypes.byref(count), item_size) != 0:
        return []
    items = (RAWINPUTDEVICELIST * count.value)()
    result = user32.GetRawInputDeviceList(items, ctypes.byref(count), item_size)
    if result < 0:
        return []
    kinds = {RIM_TYPEMOUSE: "mouse", RIM_TYPEKEYBOARD: "keyboard", RIM_TYPEHID: "hid"}
    devices: list[RawInputDevice] = []
    for item in items[: result]:
        path = _device_name(item.hDevice)
        if not path:
            continue
        info = _device_info(item.hDevice)
        is_hid = bool(info and item.dwType == RIM_TYPEHID)
        devices.append(
            RawInputDevice(
                path,
                kinds.get(item.dwType, "unknown"),
                info.hid.usUsagePage if is_hid else None,
                info.hid.usUsage if is_hid else None,
            )
        )
    return devices


class RawInputListener:
    """Hidden-window Raw Input listener; observes input and never blocks it."""

    def __init__(
        self,
        on_event: Callable[[InputEvent], None],
        on_tick: Callable[[float], None],
        stop_event: threading.Event,
        log: Callable[[str], None],
        extra_hid_usages: tuple[tuple[int, int], ...] = (),
    ):
        _require_windows()
        self._on_event = on_event
        self._on_tick = on_tick
        self._stop_event = stop_event
        self._log = log
        self._extra_hid_usages = extra_hid_usages
        self._device_names: dict[int, str] = {}
        self._hid_pressed: dict[int, set[int]] = {}
        self._hwnd = None
        self._wndproc_ref = None

    def run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        def wndproc(hwnd, message, wparam, lparam):
            try:
                if message == WM_INPUT:
                    self._handle_raw_input(lparam)
                    return 0
                if message == WM_INPUT_DEVICE_CHANGE:
                    self._device_names.clear()
                    return 0
                if message == WM_TIMER:
                    now = time.monotonic()
                    self._on_tick(now)
                    if self._stop_event.is_set():
                        user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as exc:
                self._log(f"raw-input callback failed: {exc}")
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._wndproc_ref = WNDPROC(wndproc)
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        class_name = f"RunLightsRemoteBridge_{os.getpid()}"
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc_ref
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError()
        self._hwnd = user32.CreateWindowExW(
            0, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None
        )
        if not self._hwnd:
            raise ctypes.WinError()
        usages = [(0x01, 0x06), (0x0C, 0x01), *self._extra_hid_usages]
        usages = list(dict.fromkeys(usages))
        devices = (RAWINPUTDEVICE * len(usages))(
            *(
                RAWINPUTDEVICE(
                    page,
                    usage,
                    RIDEV_INPUTSINK
                    | RIDEV_DEVNOTIFY
                    | (RIDEV_PAGEONLY if usage == 0 else 0),
                    self._hwnd,
                )
                for page, usage in usages
            )
        )
        if not user32.RegisterRawInputDevices(devices, len(devices), ctypes.sizeof(RAWINPUTDEVICE)):
            raise ctypes.WinError()
        user32.SetTimer(self._hwnd, 1, 100, None)
        self._log("raw-input listener ready")

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self._hwnd = None

    def _handle_raw_input(self, raw_handle: int) -> None:
        user32 = ctypes.windll.user32
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if user32.GetRawInputData(raw_handle, RID_INPUT, None, ctypes.byref(size), header_size) != 0:
            return
        buffer = ctypes.create_string_buffer(size.value)
        if user32.GetRawInputData(
            raw_handle, RID_INPUT, buffer, ctypes.byref(size), header_size
        ) != size.value:
            return
        header = RAWINPUTHEADER.from_buffer_copy(buffer.raw)
        device_handle = int(header.hDevice or 0)
        path = self._device_names.get(device_handle)
        if path is None:
            path = _device_name(header.hDevice)
            self._device_names[device_handle] = path
        now = time.monotonic()
        data_offset = header_size
        if header.dwType == RIM_TYPEKEYBOARD:
            keyboard = RAWKEYBOARD.from_buffer_copy(
                buffer.raw[data_offset : data_offset + ctypes.sizeof(RAWKEYBOARD)]
            )
            control = KEYBOARD_CONTROLS.get(keyboard.VKey)
            if control:
                self._on_event(
                    InputEvent(path, control, not bool(keyboard.Flags & RI_KEY_BREAK), now)
                )
        elif header.dwType == RIM_TYPEHID and size.value >= data_offset + 8:
            size_hid = int.from_bytes(buffer.raw[data_offset : data_offset + 4], "little")
            count = int.from_bytes(buffer.raw[data_offset + 4 : data_offset + 8], "little")
            start = data_offset + 8
            for index in range(count):
                report = buffer.raw[start + index * size_hid : start + (index + 1) * size_hid]
                self._handle_hid_report(device_handle, path, report, now)

    def _handle_hid_report(self, handle: int, path: str, report: bytes, now: float) -> None:
        usages = {
            int.from_bytes(report[offset : offset + 2], "little")
            for offset in range(max(0, len(report) - 1))
            if int.from_bytes(report[offset : offset + 2], "little")
            in KNOWN_CONSUMER_USAGES
        }
        previous = self._hid_pressed.get(handle, set())
        for usage in sorted(previous - usages):
            self._on_event(InputEvent(path, f"consumer_{usage:04x}", False, now))
        for usage in sorted(usages - previous):
            self._on_event(InputEvent(path, f"consumer_{usage:04x}", True, now))
        self._hid_pressed[handle] = usages
        if not usages and any(report):
            # Vendor-defined reports are exposed only as device-scoped hex for
            # diagnostics. They are never decoded as or logged as keyboard text.
            self._on_event(InputEvent(path, f"vendor_{report[:32].hex()}", True, now))


def foreground_executable() -> str | None:
    try:
        import psutil  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return (psutil.Process(pid).name() or "").casefold() or None
    except Exception:
        return None
