from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .activation import ProfileActivator
from .backends import DiagnosticBackend, OutputBackend, create_backend
from .key_state import KeyStateTracker
from .models import AppProfile, InputEvent, RemoteBridgeConfig
from .win32_raw_input import RawInputListener, enumerate_devices, foreground_executable


class RemoteBridgeEngine:
    def __init__(
        self,
        config: RemoteBridgeConfig,
        state_dir: Path,
        log,
        stop_event: threading.Event | None = None,
        parent_pid: int | None = None,
    ):
        self.config = config
        self.state_dir = state_dir
        self.log = log
        self.stop_event = stop_event or threading.Event()
        self.parent_pid = parent_pid
        self.disable_file = state_dir / "remote-bridge.disable"
        self.stop_file = state_dir / "remote-bridge.stop"
        self.status_file = state_dir / "remote-bridge-status.json"
        self.device = config.selected_device()
        self.tracker = KeyStateTracker(config.repeat_delay_ms, config.repeat_interval_ms)
        self.activator = ProfileActivator(config.profiles, self._profile_changed)
        self.backend: OutputBackend | None = None
        self._last_foreground: str | None = None
        self._escape_pressed: dict[str, float] = {}
        self._disabled_logged = False

    def run(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.device is None:
            raise RuntimeError(f"configured remote device not found: {self.config.device_id}")
        self._write_status("starting")
        extra_usages: list[tuple[int, int]] = []
        try:
            extra_usages = [
                (item.usage_page, item.usage)
                for item in enumerate_devices()
                if self.device.matches(item.path)
                and item.kind == "hid"
                and item.usage_page is not None
                and item.usage is not None
                and (item.usage_page, item.usage) != (0x0C, 0x01)
            ]
        except Exception as exc:
            self.log(f"vendor HID discovery unavailable: {exc}")
        listener = RawInputListener(
            self.handle_input,
            self.tick,
            self.stop_event,
            self.log,
            tuple(extra_usages),
        )
        try:
            listener.run()
        finally:
            self._deactivate()
            self._write_status("stopped")

    def handle_input(self, event: InputEvent) -> None:
        if self.device is None or not self.device.matches(event.device_path):
            return
        state = "down" if event.pressed else "up"
        self.log(f"input device={self.device.id} control={event.control} state={state}")
        if event.control in {"consumer_0223", "consumer_0224"}:
            if event.pressed:
                self._escape_pressed.setdefault(event.control, event.timestamp)
            else:
                self._escape_pressed.pop(event.control, None)

        profile = self.activator.active
        if profile is None or self.disable_file.exists():
            return
        action = profile.bindings.get(event.control)
        if not action or action in {"passthrough", "ignore"}:
            return
        for action_event in self.tracker.update(action, event.pressed, event.timestamp):
            if self.backend is not None:
                self.backend.emit(action_event)

    def tick(self, now: float) -> None:
        if self.stop_file.exists():
            self.log("supervisor requested clean shutdown")
            self.stop_event.set()
            return
        if self.parent_pid is not None:
            try:
                import psutil  # type: ignore

                if not psutil.pid_exists(self.parent_pid):
                    self.log("RunLights parent exited; shutting down bridge")
                    self.stop_event.set()
                    return
            except Exception:
                pass
        if self.disable_file.exists():
            if not self._disabled_logged:
                self.log("bridge disabled by stock-input escape")
                self._disabled_logged = True
            self.activator.update(None)
            self._write_status("disabled")
            return
        self._disabled_logged = False
        if len(self._escape_pressed) == 2:
            held_since = max(self._escape_pressed.values())
            if now - held_since >= self.config.escape_hold_seconds:
                self.disable_file.touch(exist_ok=True)
                self.log("Home+Back escape held; bridge disabled immediately")
                self.activator.update(None)
                self._write_status("disabled")
                return

        foreground = foreground_executable()
        if foreground != self._last_foreground:
            self._last_foreground = foreground
            self.log(f"foreground executable={foreground or '(none)'}")
        profile = self.activator.update(foreground)
        if profile is not None and self.backend is not None:
            for event in self.tracker.repeats(now):
                self.backend.emit(event)

    def _profile_changed(self, profile: AppProfile | None) -> None:
        self._deactivate()
        if profile is None:
            self.log("profile inactive")
            self._write_status("idle")
            return
        self.log(f"profile active id={profile.id}")
        backend_name = profile.backend or self.config.backend
        try:
            backend = create_backend(backend_name, self.log)
            backend.connect()
            self.backend = backend
            self._write_status("active", profile.id, backend.name)
        except Exception as exc:
            self.log(f"output backend unavailable name={backend_name}: {exc}")
            fallback = DiagnosticBackend(self.log)
            fallback.connect()
            self.backend = fallback
            self._write_status("diagnostic", profile.id, fallback.name, str(exc))

    def _deactivate(self) -> None:
        now = time.monotonic()
        if self.backend is not None:
            for event in self.tracker.release_all(now):
                self.backend.emit(event)
            try:
                self.backend.disconnect()
            finally:
                self.backend = None
        else:
            self.tracker.release_all(now)

    def _write_status(
        self,
        state: str,
        profile: str | None = None,
        backend: str | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "state": state,
            "profile": profile,
            "backend": backend,
            "device": self.config.device_id,
            "pid": __import__("os").getpid(),
            "updated": time.time(),
        }
        if error:
            payload["error"] = error
        try:
            temporary = self.status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(self.status_file)
        except Exception:
            pass
