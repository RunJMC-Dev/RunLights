from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from runlights.config import load_config
from runlights.remote.config import load_remote_bridge_config


class RemoteBridgeModule:
    """Supervises the isolated Runlights.RemoteBridge companion process."""

    name = "remote-bridge"

    def __init__(self, config_path: Path, app_root: Path, log: Callable[[str], None]):
        self._config_path = config_path
        self._app_root = app_root
        self._state_dir = app_root / ".runlights"
        self._log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._last_mtime_ns: int | None = None

    @property
    def process(self) -> subprocess.Popen[str] | None:
        return self._process

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_process()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

    def _enabled(self) -> bool:
        try:
            return load_remote_bridge_config(load_config(self._config_path).raw).enabled
        except Exception as exc:
            self._log(f"Remote bridge config unavailable: {exc}")
            return False

    def _supervise(self) -> None:
        retry_at = 0.0
        while not self._stop.wait(0.5):
            try:
                mtime_ns = self._config_path.stat().st_mtime_ns
            except OSError:
                mtime_ns = None
            if self._last_mtime_ns is None:
                self._last_mtime_ns = mtime_ns
            elif mtime_ns != self._last_mtime_ns:
                self._last_mtime_ns = mtime_ns
                self._log("Remote bridge configuration changed; restarting companion")
                self._stop_process()

            enabled = self._enabled()
            if not enabled:
                self._stop_process()
                continue
            if self._process is not None and self._process.poll() is not None:
                code = self._process.returncode
                self._log(f"Remote bridge exited with code {code}; retrying safely")
                self._process = None
                retry_at = time.monotonic() + 2.0
            if self._process is None and time.monotonic() >= retry_at:
                self._start_process()

    def _start_process(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        stop_marker = self._state_dir / "remote-bridge.stop"
        if stop_marker.exists():
            stop_marker.unlink()
        env = os.environ.copy()
        source_path = str(self._app_root / "src")
        env["PYTHONPATH"] = source_path + os.pathsep + env.get("PYTHONPATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "runlights.remote_bridge",
                "--config",
                str(self._config_path),
                "--state-dir",
                str(self._state_dir),
                "--parent-pid",
                str(os.getpid()),
            ],
            cwd=str(self._app_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            text=True,
        )
        self._log(f"Remote bridge companion started pid={self._process.pid}")

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        stop_marker = self._state_dir / "remote-bridge.stop"
        stop_marker.touch(exist_ok=True)
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        finally:
            if stop_marker.exists():
                stop_marker.unlink()
        self._log("Remote bridge companion stopped")


def set_stock_input_escape(app_root: Path, disabled: bool) -> None:
    state_dir = app_root / ".runlights"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / "remote-bridge.disable"
    if disabled:
        marker.touch(exist_ok=True)
    elif marker.exists():
        marker.unlink()
