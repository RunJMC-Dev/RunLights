from __future__ import annotations

import importlib.util
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
    return DiagnosticBackend(log)
