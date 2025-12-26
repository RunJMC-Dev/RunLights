from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from queue import Queue

try:
    import win32file
    import win32pipe
    import pywintypes
except ImportError as exc:  # pragma: no cover - platform/dependency guard
    raise ImportError("Windows named pipe IPC requires pywin32 (win32file/win32pipe)") from exc

from .config import ConfigError, load_config
from .ipc import PIPE_NAME

log = logging.getLogger("runlights.tray")


def _create_pipe():
    """Create a named pipe, retrying if instances are temporarily busy."""
    while True:
        try:
            return win32pipe.CreateNamedPipe(
                PIPE_NAME,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                4,  # allow a few concurrent instances
                65536,
                65536,
                0,
                None,
            )
        except pywintypes.error as exc:
            if exc.winerror == 231:  # all pipe instances are busy
                time.sleep(0.1)
                continue
            raise


def serve(
    config_path: Path | str = "config.toml",
    stop_event: threading.Event | None = None,
    log_queue: Optional[Queue[str]] = None,
) -> None:
    """Start the tray IPC server (blocking) on a Windows named pipe."""
    if log_queue is not None:
        try:
            log_queue.put(f"Tray IPC listening on {PIPE_NAME}")
        except Exception:
            pass
    while True:
        if stop_event and stop_event.is_set():
            log.info("Tray IPC stop requested")
            break
        pipe = _create_pipe()
        try:
            win32pipe.ConnectNamedPipe(pipe, None)
            raw = _read_message(pipe)
            if not raw:
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
            except Exception:
                _send_with_log(pipe, {"status": "error", "error": "invalid_json"}, log_queue, raw=raw.decode(errors="ignore"))
                continue

            if message.get("type") != "console":
                _send_with_log(pipe, {"status": "error", "error": "unsupported_type"}, log_queue, raw=message)
                continue

            console_name = message.get("name")
            if not console_name:
                _send_with_log(pipe, {"status": "error", "error": "missing_name"}, log_queue, raw=message)
                continue

            try:
                config = load_config(Path(config_path))
            except ConfigError as exc:
                if log_queue is not None:
                    try:
                        log_queue.put(f"Config error: {exc}")
                    except Exception:
                        pass
                _send_with_log(pipe, {"status": "error", "error": str(exc)}, log_queue, raw=message)
                continue

            binding = config.find_esde_binding(console_name)
            if binding is None:
                _send_with_log(pipe, {"status": "error", "error": f"console '{console_name}' not found"}, log_queue, raw=message)
                continue

            log.info("Received console request: %s -> %s", console_name, binding)
            _send_with_log(pipe, {"status": "ok", "binding": binding, "console": console_name}, log_queue, raw=message)
        except KeyboardInterrupt:
            log.info("Tray IPC shutting down")
            break
        except pywintypes.error as exc:
            log.error("Pipe error: %s", exc)
        finally:
            try:
                win32pipe.DisconnectNamedPipe(pipe)
            except Exception:
                pass
            try:
                win32file.CloseHandle(pipe)
            except Exception:
                pass


def serve_in_thread(
    config_path: Path | str = "config.toml",
    stop_event: threading.Event | None = None,
    log_queue: Optional[Queue[str]] = None,
) -> threading.Thread:
    """Start the tray IPC server in a background thread."""
    thread = threading.Thread(target=serve, args=(config_path, stop_event, log_queue), daemon=True)
    thread.start()
    return thread


def _read_message(pipe_handle) -> bytes:
    """Read a single message terminated by newline."""
    chunks: list[bytes] = []
    while True:
        try:
            _, data = win32file.ReadFile(pipe_handle, 4096)
        except pywintypes.error as exc:
            if exc.winerror == 109:  # pipe closed
                break
            raise
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break
    return b"".join(chunks)


def _send_message(pipe_handle, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8") + b"\n"
    win32file.WriteFile(pipe_handle, data)
    win32file.FlushFileBuffers(pipe_handle)


def _send_with_log(pipe_handle, payload: Dict[str, Any], log_queue: Optional[Queue[str]], raw: Any = None) -> None:
    _send_message(pipe_handle, payload)
    if log_queue is None:
        return
    try:
        if raw is not None:
            log_queue.put(f"CLI recv: {raw}")
        log_queue.put(f"CLI send: {payload}")
    except Exception:
        pass
