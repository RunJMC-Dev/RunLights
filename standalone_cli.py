from __future__ import annotations

import json
import sys

PIPE_NAME = r"\\.\pipe\runlights_ipc"


def send_console(name: str) -> int:
    try:
        import win32file  # type: ignore
        import pywintypes  # type: ignore
    except Exception:
        # Fail quietly to avoid breaking the caller.
        return 0

    payload = (json.dumps({"type": "console", "name": name}) + "\n").encode("utf-8")
    try:
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    except Exception:
        return 0

    try:
        win32file.WriteFile(handle, payload)
        _, _ = win32file.ReadFile(handle, 4096)
    except Exception:
        return 0
    finally:
        try:
            handle.Close()
        except Exception:
            pass
    return 0


def main() -> int:
    # ES-DE passes args; system name typically in argv[3]
    if len(sys.argv) >= 4:
        console = sys.argv[3].strip().lower()
    elif len(sys.argv) == 2:
        console = sys.argv[1].strip().lower()
    else:
        return 0
    return send_console(console)


if __name__ == "__main__":
    raise SystemExit(main())
