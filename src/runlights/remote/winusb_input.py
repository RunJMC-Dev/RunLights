from __future__ import annotations

import ctypes
import os
import threading
import time
import uuid
from ctypes import wintypes
from typing import Callable

from .models import DeviceDefinition, InputEvent


CR_SUCCESS = 0
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_OPERATION_ABORTED = 995
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
USB_ENDPOINT_DIRECTION_MASK = 0x80
USBD_PIPE_TYPE_INTERRUPT = 3

KEYBOARD_USAGES = {
    0x28: "enter",
    0x29: "escape",
    0x4B: "page_up",
    0x4E: "page_down",
    0x4F: "right",
    0x50: "left",
    0x51: "down",
    0x52: "up",
}

CONSUMER_USAGES = {
    0x0041,
    0x00CD,
    0x00CF,
    0x00E2,
    0x00E9,
    0x00EA,
    0x0223,
    0x0224,
}


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        raw = uuid.UUID(value.strip("{}")).bytes_le
        return cls.from_buffer_copy(raw)


class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bInterfaceNumber", ctypes.c_ubyte),
        ("bAlternateSetting", ctypes.c_ubyte),
        ("bNumEndpoints", ctypes.c_ubyte),
        ("bInterfaceClass", ctypes.c_ubyte),
        ("bInterfaceSubClass", ctypes.c_ubyte),
        ("bInterfaceProtocol", ctypes.c_ubyte),
        ("iInterface", ctypes.c_ubyte),
    ]


class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", wintypes.USHORT),
        ("Interval", ctypes.c_ubyte),
    ]


class HidPacketDecoder:
    """Decode the receiver's keyboard/consumer reports without text logging."""

    def __init__(self):
        self._pressed: dict[int, set[str]] = {}

    def decode(self, pipe_id: int, report: bytes, path: str, now: float) -> list[InputEvent]:
        controls: set[str] = set()
        # HID boot keyboard reports are eight bytes, optionally prefixed with a
        # report ID. Only the named non-text navigation usages are accepted.
        candidates = report[1:] if len(report) >= 9 else report
        if len(candidates) >= 8:
            for usage in candidates[2:8]:
                control = KEYBOARD_USAGES.get(usage)
                if control:
                    controls.add(control)
        for offset in range(max(0, len(report) - 1)):
            usage = int.from_bytes(report[offset : offset + 2], "little")
            if usage in CONSUMER_USAGES:
                controls.add(f"consumer_{usage:04x}")
        previous = self._pressed.get(pipe_id, set())
        events = [InputEvent(path, item, False, now) for item in sorted(previous - controls)]
        events.extend(InputEvent(path, item, True, now) for item in sorted(controls - previous))
        self._pressed[pipe_id] = controls
        return events

    def release_all(self, path: str, now: float) -> list[InputEvent]:
        events = [
            InputEvent(path, item, False, now)
            for controls in self._pressed.values()
            for item in sorted(controls)
        ]
        self._pressed.clear()
        return events


def enumerate_winusb_paths(interface_guid: str) -> list[str]:
    if os.name != "nt":
        return []
    cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    guid = GUID.parse(interface_guid)
    size = wintypes.ULONG(0)
    get_size = cfgmgr32.CM_Get_Device_Interface_List_SizeW
    get_size.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.ULONG]
    get_size.restype = wintypes.ULONG
    result = get_size(ctypes.byref(size), ctypes.byref(guid), None, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
    if result != CR_SUCCESS or size.value <= 1:
        return []
    buffer = ctypes.create_unicode_buffer(size.value)
    get_list = cfgmgr32.CM_Get_Device_Interface_ListW
    get_list.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]
    get_list.restype = wintypes.ULONG
    result = get_list(ctypes.byref(guid), None, buffer, size.value, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
    if result != CR_SUCCESS:
        return []
    return [item for item in buffer[: size.value].split("\0") if item]


class WinUsbInputListener:
    """Direct reader used only after MI_02 is explicitly rebound to WinUSB."""

    def __init__(
        self,
        device: DeviceDefinition,
        on_event: Callable[[InputEvent], None],
        on_tick: Callable[[float], None],
        stop_event: threading.Event,
        log: Callable[[str], None],
    ):
        if os.name != "nt":
            raise RuntimeError("WinUSB input is only available on Windows")
        self._device = device
        self._on_event = on_event
        self._on_tick = on_tick
        self._stop_event = stop_event
        self._log = log
        self._file = wintypes.HANDLE()
        self._interface = ctypes.c_void_p()
        self._decoder = HidPacketDecoder()
        self._threads: list[threading.Thread] = []
        self._path = ""

    def run(self) -> None:
        try:
            self._open()
            while not self._stop_event.wait(0.1):
                self._on_tick(time.monotonic())
                if self._threads and not any(thread.is_alive() for thread in self._threads):
                    raise RuntimeError("all WinUSB interrupt readers stopped")
        finally:
            self._close()

    def _open(self) -> None:
        paths = [path for path in enumerate_winusb_paths(self._device.interface_guid) if self._device.matches(path)]
        if len(paths) != 1:
            raise RuntimeError(
                f"expected one owned WinUSB interface for {self._device.id}, found {len(paths)}"
            )
        self._path = paths[0]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._file = kernel32.CreateFileW(
            self._path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if self._file == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        winusb = ctypes.WinDLL("winusb", use_last_error=True)
        winusb.WinUsb_Initialize.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        winusb.WinUsb_Initialize.restype = wintypes.BOOL
        winusb.WinUsb_QueryInterfaceSettings.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.POINTER(USB_INTERFACE_DESCRIPTOR),
        ]
        winusb.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
        winusb.WinUsb_QueryPipe.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.c_ubyte,
            ctypes.POINTER(WINUSB_PIPE_INFORMATION),
        ]
        winusb.WinUsb_QueryPipe.restype = wintypes.BOOL
        if not winusb.WinUsb_Initialize(self._file, ctypes.byref(self._interface)):
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = USB_INTERFACE_DESCRIPTOR()
        if not winusb.WinUsb_QueryInterfaceSettings(self._interface, 0, ctypes.byref(descriptor)):
            raise ctypes.WinError(ctypes.get_last_error())
        pipes: list[WINUSB_PIPE_INFORMATION] = []
        for index in range(descriptor.bNumEndpoints):
            info = WINUSB_PIPE_INFORMATION()
            if winusb.WinUsb_QueryPipe(self._interface, 0, index, ctypes.byref(info)):
                if info.PipeType == USBD_PIPE_TYPE_INTERRUPT and info.PipeId & USB_ENDPOINT_DIRECTION_MASK:
                    pipes.append(info)
        if not pipes:
            raise RuntimeError("owned interface has no interrupt IN endpoint")
        for info in pipes:
            thread = threading.Thread(
                target=self._read_pipe,
                args=(info.PipeId, max(64, int(info.MaximumPacketSize))),
                name=f"remote-winusb-{info.PipeId:02x}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
        self._log(f"owned WinUSB listener ready interface=MI_02 endpoints={len(pipes)}")

    def _read_pipe(self, pipe_id: int, packet_size: int) -> None:
        winusb = ctypes.WinDLL("winusb", use_last_error=True)
        winusb.WinUsb_ReadPipe.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte),
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
            ctypes.c_void_p,
        ]
        winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
        while not self._stop_event.is_set():
            buffer = (ctypes.c_ubyte * packet_size)()
            transferred = wintypes.ULONG(0)
            ok = winusb.WinUsb_ReadPipe(
                self._interface,
                pipe_id,
                buffer,
                packet_size,
                ctypes.byref(transferred),
                None,
            )
            if not ok:
                error = ctypes.get_last_error()
                if self._stop_event.is_set() or error == ERROR_OPERATION_ABORTED:
                    return
                self._log(f"WinUSB read stopped endpoint=0x{pipe_id:02x} error={error}")
                return
            report = bytes(buffer[: transferred.value])
            now = time.monotonic()
            events = self._decoder.decode(pipe_id, report, self._path, now)
            if not events and any(report):
                self._log(f"owned input unknown endpoint=0x{pipe_id:02x} report={report[:32].hex()}")
            for event in events:
                self._on_event(event)

    def _close(self) -> None:
        for event in self._decoder.release_all(self._path, time.monotonic()):
            self._on_event(event)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if self._file and self._file != INVALID_HANDLE_VALUE:
            try:
                kernel32.CancelIoEx(self._file, None)
            except Exception:
                pass
        for thread in self._threads:
            thread.join(timeout=1.0)
        if self._interface:
            try:
                ctypes.WinDLL("winusb", use_last_error=True).WinUsb_Free(self._interface)
            except Exception:
                pass
        if self._file and self._file != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self._file)
        self._threads.clear()
        self._interface = ctypes.c_void_p()
        self._file = wintypes.HANDLE()
