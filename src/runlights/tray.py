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
from . import wled

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
            apply_err = _apply_segmentsolid(binding, config.raw)
            if apply_err:
                _send_with_log(pipe, {"status": "error", "error": apply_err}, log_queue, raw=message)
                continue
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


def _apply_segmentsolid(binding: Dict[str, Any], cfg_raw: dict) -> Optional[str]:
    """
    Apply the segmentsolid output for ESDE game-select based on bindings and mode config.
    """
    try:
        mode = next(
            m for app in cfg_raw.get("application", []) if app.get("id") == "esde"
            for m in app.get("modes", []) if m.get("id") == "game-select"
        )
    except StopIteration:
        return "esde game-select mode not found"

    controllers_filter = mode.get("controllers", [])
    acolor = mode.get("acolor", "#000000")
    bcolor = mode.get("bcolor", "#000000")
    try:
        abri = int(mode.get("abrightness", 0))
        bbri = int(mode.get("bbrightness", 0))
    except Exception:
        return "Invalid brightness values"
    transition_ms = mode.get("transition_ms", cfg_raw.get("default_transition_ms"))

    target_controller = binding.get("controller")
    target_segment = binding.get("segment")
    if target_controller is None or target_segment is None:
        return "Binding missing controller/segment"

    for ctrl_entry in cfg_raw.get("controllers", []):
        cid = ctrl_entry.get("id")
        if controllers_filter and cid not in controllers_filter:
            continue
        chost = ctrl_entry.get("host")
        cport = int(ctrl_entry.get("port", 80))
        segments = ctrl_entry.get("segments", [])
        if not segments:
            continue
        seg_updates = []
        for seg in segments:
            seg_id = seg.get("id")
            is_target = cid == target_controller and seg_id == target_segment
            seg_color = acolor if is_target else bcolor
            seg_bri = abri if is_target else bbri
            seg_on = seg_bri > 0
            seg_updates.append(
                wled.WLEDPayload(
                    on=seg_on,
                    brightness=seg_bri,
                    color=wled._hex_to_rgb(seg_color),
                    segment=seg_id,
                )
            )
        try:
            wled.send_batch(
                controller=wled.WLEDController(host=chost, port=cport),
                seg_updates=seg_updates,
                transition_ms=transition_ms,
            )
        except Exception as exc:
            return f"WLED error on {cid}: {exc}"
    return None
