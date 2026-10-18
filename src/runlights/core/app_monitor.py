from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .events import EventBus


@dataclass(frozen=True)
class ApplicationStarted:
    app_id: str


@dataclass(frozen=True)
class ApplicationStopped:
    app_id: str


@dataclass(frozen=True)
class ForegroundApplicationChanged:
    app_id: str | None
    executable: str | None


class ApplicationMonitorModule:
    """Publishes process and foreground changes for all interested modules."""

    name = "application-monitor"

    def __init__(
        self,
        applications: list[dict],
        event_bus: EventBus,
        log: Callable[[str], None],
        poll_seconds: float = 0.5,
        pause_event: threading.Event | None = None,
    ):
        self._event_bus = event_bus
        self._log = log
        self._poll_seconds = poll_seconds
        self._pause_event = pause_event
        self._process_to_app: dict[str, str] = {}
        for app in applications:
            app_id = str(app.get("id", "")).strip()
            for process in app.get("processes", []):
                if app_id and isinstance(process, str):
                    self._process_to_app[process.casefold()] = app_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._process_to_app or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            import psutil  # type: ignore
            import win32gui  # type: ignore
            import win32process  # type: ignore
        except Exception as exc:
            self._log(f"Application monitor unavailable: {exc}")
            return

        seen: set[str] = set()
        last_foreground: tuple[str | None, str | None] = (None, None)
        while not self._stop.wait(self._poll_seconds):
            if self._pause_event is not None and self._pause_event.is_set():
                continue
            try:
                running: set[str] = set()
                for proc in psutil.process_iter(["name"]):
                    name = (proc.info.get("name") or "").casefold()
                    app_id = self._process_to_app.get(name)
                    if app_id:
                        running.add(app_id)
                for app_id in sorted(running - seen):
                    self._event_bus.publish(ApplicationStarted(app_id))
                for app_id in sorted(seen - running):
                    self._event_bus.publish(ApplicationStopped(app_id))
                seen = running

                executable: str | None = None
                app_id: str | None = None
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    executable = (psutil.Process(pid).name() or "").casefold() or None
                    app_id = self._process_to_app.get(executable or "")
                foreground = (app_id, executable)
                if foreground != last_foreground:
                    self._event_bus.publish(ForegroundApplicationChanged(app_id, executable))
                    last_foreground = foreground
            except Exception as exc:
                self._log(f"Application monitor poll failed: {exc}")
