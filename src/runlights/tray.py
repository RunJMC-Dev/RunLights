from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

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
    return win32pipe.CreateNamedPipe(
        PIPE_NAME,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
        1,  # max instances
        65536,
        65536,
        0,
        None,
    )


def serve(config_path: Path | str = "config.toml", stop_event: threading.Event | None = None) -> None:
    """Start the tray IPC server (blocking) on a Windows named pipe."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    log.info("Tray IPC listening on %s", PIPE_NAME)
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
                _send_message(pipe, {"status": "error", "error": "invalid_json"})
                continue

            if message.get("type") != "console":
                _send_message(pipe, {"status": "error", "error": "unsupported_type"})
                continue

            console_name = message.get("name")
            if not console_name:
                _send_message(pipe, {"status": "error", "error": "missing_name"})
                continue

            try:
                config = load_config(Path(config_path))
            except ConfigError as exc:
                _send_message(pipe, {"status": "error", "error": str(exc)})
                continue

            binding = config.find_esde_binding(console_name)
            if binding is None:
                _send_message(pipe, {"status": "error", "error": f"console '{console_name}' not found"})
                continue

            log.info("Received console request: %s -> %s", console_name, binding)
            # TODO: apply binding via WLED REST with transitions.
            _send_message(pipe, {"status": "ok", "binding": binding})
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


def serve_in_thread(config_path: Path | str = "config.toml", stop_event: threading.Event | None = None) -> threading.Thread:
    """Start the tray IPC server in a background thread."""
    thread = threading.Thread(target=serve, args=(config_path, stop_event), daemon=True)
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
