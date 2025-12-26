from __future__ import annotations

import json
from typing import Any, Dict


class IPCNotReady(Exception):
    """Raised when the tray IPC endpoint is not available."""


def send_console_request(console_name: str) -> Dict[str, Any]:
    """
    Placeholder IPC call to forward a console name to the tray app.

    TODO: replace with named pipe or TCP client that talks to the tray service.
    """
    # For now, we just raise to indicate the tray side isn't implemented yet.
    raise IPCNotReady(f"Tray IPC not implemented; attempted to send console '{console_name}'")


def format_console_message(console_name: str) -> str:
    """Return the JSON payload we will send over IPC."""
    return json.dumps({"type": "console", "name": console_name})

