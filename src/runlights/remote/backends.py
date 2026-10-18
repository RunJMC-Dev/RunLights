from __future__ import annotations

import ctypes
import importlib.util
import os
from ctypes import wintypes
from typing import Protocol

from .models import ActionEvent


class OutputBackend(Protocol):
    name: str

    def connect(self) -> None: ...
    def emit(self, event: ActionEvent) -> None: ...
    def disconnect(self) -> None: ...


class DiagnosticBackend:
    name = "diagnostic"

    def __init__(self, log):
        self._log = log

    def connect(self) -> None:
        self._log("output connected backend=diagnostic (no input is injected)")

    def emit(self, event: ActionEvent) -> None:
        state = "repeat" if event.repeat else ("down" if event.pressed else "up")
        self._log(f"action {event.action} {state}")

    def disconnect(self) -> None:
        self._log("output disconnected backend=diagnostic")


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUT_UNION)]


class SendInputBackend:
    """Emit configured Windows keys after the receiver is exclusively owned.

    This backend never hooks or filters a keyboard. The engine refuses to use
    it with the observational Raw Input source, where injection would duplicate
    the receiver's existing HID keystrokes.
    """

    name = "sendinput"
    _KEYEVENTF_KEYUP = 0x0002
    _VK = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "escape": 0x1B,
        "space": 0x20,
        "page_up": 0x21,
        "page_down": 0x22,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "home": 0x24,
        "volume_mute": 0xAD,
        "volume_down": 0xAE,
        "volume_up": 0xAF,
        "media_next": 0xB0,
        "media_previous": 0xB1,
        "media_stop": 0xB2,
        "media_play_pause": 0xB3,
    }
    _MODIFIERS = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12}

    def __init__(self, log):
        self._log = log
        self._held: dict[str, tuple[int, ...]] = {}

    def connect(self) -> None:
        if os.name != "nt":
            raise RuntimeError("SendInput is only available on Windows")
        self._log("key output connected backend=sendinput")

    @classmethod
    def _keys_for_action(cls, action: str) -> tuple[int, ...] | None:
        if not action.startswith("key_"):
            return None
        tokens = action[4:].split("+")
        if not tokens:
            return None
        keys: list[int] = []
        for token in tokens:
            key = cls._MODIFIERS.get(token, cls._VK.get(token))
            if key is None and len(token) == 1 and token.isascii() and token.isalnum():
                key = ord(token.upper())
            if key is None and token.startswith("f") and token[1:].isdigit():
                function_number = int(token[1:])
                if 1 <= function_number <= 24:
                    key = 0x6F + function_number
            if key is None:
                return None
            keys.append(key)
        return tuple(keys)

    @staticmethod
    def _send(vk: int, pressed: bool) -> None:
        flags = 0 if pressed else SendInputBackend._KEYEVENTF_KEYUP
        item = _INPUT(type=1, ki=_KEYBDINPUT(vk, 0, flags, 0, 0))
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise ctypes.WinError()

    def emit(self, event: ActionEvent) -> None:
        if event.action in {"ignore", "passthrough"}:
            return
        keys = self._keys_for_action(event.action)
        if keys is None:
            self._log(f"unmapped key output action={event.action}")
            return
        if event.repeat:
            self._send(keys[-1], True)
            return
        if event.pressed:
            if event.action in self._held:
                return
            for key in keys:
                self._send(key, True)
            self._held[event.action] = keys
            return
        held = self._held.pop(event.action, keys)
        for key in reversed(held):
            self._send(key, False)

    def disconnect(self) -> None:
        for keys in list(self._held.values()):
            for key in reversed(keys):
                try:
                    self._send(key, False)
                except Exception:
                    pass
        self._held.clear()
        self._log("key output disconnected backend=sendinput")


class VGamepadBackend:
    """Optional legacy backend; never installs ViGEmBus or Python packages."""

    name = "vgamepad"

    _BUTTON_NAMES = {
        "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
        "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
        "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
        "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
        "button_a": "XUSB_GAMEPAD_A",
        "button_b": "XUSB_GAMEPAD_B",
        "button_x": "XUSB_GAMEPAD_X",
        "button_y": "XUSB_GAMEPAD_Y",
        "left_shoulder": "XUSB_GAMEPAD_LEFT_SHOULDER",
        "right_shoulder": "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "guide": "XUSB_GAMEPAD_GUIDE",
    }

    def __init__(self, log):
        self._log = log
        self._module = None
        self._pad = None

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("vgamepad") is not None

    def connect(self) -> None:
        if not self.available():
            raise RuntimeError(
                "vgamepad is not installed; RunLights will not install the retired ViGEmBus stack automatically"
            )
        import vgamepad as vg  # type: ignore

        self._module = vg
        self._pad = vg.VX360Gamepad()
        self._pad.reset()
        self._pad.update()
        self._log("virtual Xbox controller connected")

    def emit(self, event: ActionEvent) -> None:
        if self._pad is None or self._module is None or event.repeat:
            return
        if event.action in {"ignore", "passthrough"}:
            return
        member = self._BUTTON_NAMES.get(event.action)
        if member is None:
            self._log(f"unmapped output action={event.action}")
            return
        button = getattr(self._module.XUSB_BUTTON, member, None)
        if button is None:
            self._log(f"backend does not support action={event.action}")
            return
        if event.pressed:
            self._pad.press_button(button=button)
        else:
            self._pad.release_button(button=button)
        self._pad.update()

    def disconnect(self) -> None:
        if self._pad is not None:
            try:
                self._pad.reset()
                self._pad.update()
            finally:
                self._pad = None
                self._module = None
                self._log("virtual Xbox controller disconnected")


def create_backend(name: str, log) -> OutputBackend:
    if name == "vgamepad":
        return VGamepadBackend(log)
    if name == "sendinput":
        return SendInputBackend(log)
    return DiagnosticBackend(log)
