from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

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


def _pythonw_path() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _restart_runlights() -> int:
    # Best-effort: only target processes whose cmdline includes runlights.pyw
    procs = []
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if "runlights.pyw" in cmdline:
                procs.append(proc)
    except Exception:
        procs = []

    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in procs:
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    runlights_path = Path(__file__).resolve().parent / "runlights.pyw"
    if not runlights_path.exists():
        return 0
    try:
        subprocess.Popen(
            [_pythonw_path(), str(runlights_path)],
            cwd=str(runlights_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    except Exception:
        return 0
    return 0


def main() -> int:
    # ES-DE passes args; system name typically in argv[3]
    if len(sys.argv) == 2 and sys.argv[1].strip().lower() == "restart":
        return _restart_runlights()
    if len(sys.argv) >= 4:
        console = sys.argv[3].strip().lower()
    elif len(sys.argv) == 2:
        console = sys.argv[1].strip().lower()
    else:
        return 0
    return send_console(console)


if __name__ == "__main__":
    raise SystemExit(main())
