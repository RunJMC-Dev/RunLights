from __future__ import annotations

import json
from typing import Any, Dict

try:
    import win32file
    import win32pipe
    import pywintypes
except ImportError as exc:  # pragma: no cover - platform/dependency guard
    raise ImportError("Windows named pipe IPC requires pywin32 (win32file/win32pipe)") from exc


class IPCNotReady(Exception):
    """Raised when the tray IPC endpoint is not available."""


PIPE_NAME = r"\\.\pipe\runlights_ipc"


def send_console_request(console_name: str) -> Dict[str, Any]:
    """
    Send a console request to the tray app over a Windows named pipe.

    Returns the parsed JSON response.
    """
    payload = (format_console_message(console_name) + "\n").encode("utf-8")
    try:
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,  # no sharing
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    except pywintypes.error as exc:
        # 2 = file not found -> pipe not created yet
        if exc.winerror == 2:
            raise IPCNotReady("Tray IPC not running (pipe not found)") from exc
        raise IPCNotReady(f"Tray IPC error opening pipe: {exc}") from exc

    try:
        win32file.WriteFile(handle, payload)
        result, data = win32file.ReadFile(handle, 4096)
    except pywintypes.error as exc:
        raise IPCNotReady(f"Tray IPC read/write error: {exc}") from exc
    finally:
        handle.Close()

    try:
        return json.loads(data.decode("utf-8").strip())
    except Exception as exc:
        raise IPCNotReady(f"Tray IPC response not valid JSON: {data!r}") from exc


def format_console_message(console_name: str) -> str:
    """Return the JSON payload we will send over IPC."""
    return json.dumps({"type": "console", "name": console_name})
