from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
import queue
from datetime import datetime

# Allow running without installation by adjusting path.
_here = Path(__file__).resolve().parent
CONFIG_PATH = _here / "config.toml"
# Ensure relative paths (config/logo) work even when double-click launched.
os.chdir(_here)
sys.path.insert(0, str(_here / "src"))

from runlights.tray import serve_in_thread  # noqa: E402
from runlights.ipc import PIPE_NAME  # noqa: E402
from runlights.config import load_config, ConfigError  # noqa: E402
from runlights import wled  # noqa: E402

try:
    import pystray  # type: ignore
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pystray = None

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

# Hard-coded icon path (use bundled icon.ico if present).
ICON_PATH = _here / "icon.ico"
SINGLE_INSTANCE_PIPE = PIPE_NAME  # reuse the IPC pipe name for single-instance guard


def start_tray_icon(stop_event: threading.Event, debug_request: threading.Event) -> pystray.Icon | None:
    if pystray is None:
        logging.warning("pystray/Pillow not installed; tray icon disabled")
        return None

    icon_image = _load_icon_image()
    if icon_image is None:
        logging.warning("No icon available; tray icon disabled")
        return None

    def on_debug(icon, item):
        debug_request.set()

    def on_quit(icon, item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Debug", on_debug),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("RunLights", icon_image, "RunLights", menu=menu)
    icon.run_detached()
    return icon


def _load_icon_image():
    try:
        if ICON_PATH.exists():
            return Image.open(ICON_PATH)
    except Exception:
        logging.warning("Failed to load icon at %s", ICON_PATH)
    # Fallback: simple blue square.
    try:
        img = Image.new("RGB", (64, 64), (0, 100, 220))
        draw = ImageDraw.Draw(img)
        draw.rectangle([16, 16, 48, 48], fill=(255, 255, 255))
        return img
    except Exception:
        return None


def _run_debug_window(stop_event: threading.Event, log_queue: "queue.Queue[str]", log_buffer: list[str]):
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk
        try:
            from PIL import Image, ImageTk  # type: ignore
            try:
                RESAMPLE = Image.Resampling.LANCZOS  # Pillow >= 10
            except Exception:
                RESAMPLE = Image.ANTIALIAS  # Pillow < 10
        except Exception:
            Image = None  # type: ignore
            ImageTk = None  # type: ignore
    except Exception as exc:
        logging.warning("Cannot open debug window (tkinter not available): %s", exc)
        return

    root = tk.Tk()
    root.title("RunLights Debug")
    root.geometry("640x480")
    try:
        if ICON_PATH.exists():
            root.iconbitmap(default=str(ICON_PATH))
    except Exception as exc:
        logging.warning("Failed to set window icon: %s", exc)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    try:
        style = ttk.Style()
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
    except Exception:
        pass

    content = ttk.Frame(root, padding=8)
    content.pack(fill="both", expand=True)

    # Pull any queued messages into the buffer before rendering, avoiding duplicates.
    try:
        seen = set(log_buffer)
        while True:
            msg = log_queue.get_nowait()
            if msg not in seen:
                log_buffer.append(msg)
                seen.add(msg)
    except queue.Empty:
        pass

    if Image and ImageTk:
        logo_path = _here / "images" / "logo.png"
        if logo_path.exists():
            try:
                img = Image.open(logo_path)
                img.thumbnail((220, 180), RESAMPLE)
                photo = ImageTk.PhotoImage(img)
                logo_label = ttk.Label(content, image=photo)
                logo_label.image = photo  # keep reference
                logo_label.pack(pady=6)
            except Exception as exc:
                logging.warning("Failed to load logo %s: %s", logo_path, exc)

    ttk.Label(content, text="RunLights debug view", font=("Segoe UI", 11)).pack(pady=2)

    log_box = scrolledtext.ScrolledText(content, width=72, height=16, state="disabled", font=("Consolas", 9))
    log_box.pack(padx=6, pady=6, fill="both", expand=True)

    input_frame = ttk.Frame(content)
    input_frame.pack(fill="x", padx=6, pady=(0, 6))
    input_var = tk.StringVar()
    input_entry = ttk.Entry(input_frame, textvariable=input_var)
    input_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
    send_btn = ttk.Button(input_frame, text="Send")
    send_btn.pack(side="right")

    def append_line(line: str, preformatted: bool = False):
        if preformatted:
            text = line
            prefix_len = 0
        else:
            prefix = f"[{datetime.now().strftime('%H:%M:%S')}] "
            prefix_len = len(prefix)
            text = prefix + line
        if "\n" in text and prefix_len:
            indent = " " * prefix_len
            text = text.replace("\n", "\n" + indent)
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.configure(state="disabled")
        log_box.see("end")

    # preload existing buffer
    for entry in log_buffer:
        append_line(entry, preformatted=True)

    def handle_command(cmd: str):
        cmd = cmd.strip().lower()
        if not cmd:
            return
        if cmd == "show applications":
            result = _format_applications(cfg_raw_global) if cfg_raw_global else "(no config loaded)"
            append_line(result)
        elif cmd == "show controllers":
            result = _format_controllers(cfg_raw_global) if cfg_raw_global else "(no config loaded)"
            append_line(result)
        elif cmd.startswith("testoutput "):
            if not cfg_raw_global:
                append_line("No config loaded")
                return
            parts = cmd.split()
            if len(parts) != 3 or "." not in parts[1]:
                append_line("Usage: testoutput <app>.<mode> <value>")
                return
            app_mode = parts[1]
            raw_val = parts[2]
            app_id, mode_id = app_mode.split(".", 1)
            mode = _lookup_mode(cfg_raw_global, app_id, mode_id)
            if not mode:
                append_line(f"Mode {app_id}.{mode_id} not found")
                return
            # segmentsolid expects a binding name; others expect numeric.
            if mode.get("output") == "segmentsolid":
                val = raw_val
            else:
                try:
                    val = float(raw_val)
                except Exception:
                    append_line("Usage: testoutput <app>.<mode> <value>")
                    return
            _apply_output(mode, cfg_raw_global, val, append_line)
        else:
            append_line(f"Unknown command: {cmd}")

    def on_send(event=None):
        text = input_var.get()
        input_var.set("")
        handle_command(text)

    send_btn.configure(command=on_send)
    input_entry.bind("<Return>", on_send)

    def poll_queue():
        try:
            while True:
                line = log_queue.get_nowait()
                append_line(line, preformatted=True)
        except queue.Empty:
            pass
        if not stop_event.is_set():
            root.after(500, poll_queue)

    def poll_stop():
        if stop_event.is_set():
            try:
                root.destroy()
            except Exception:
                pass
            return
        root.after(500, poll_stop)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.after(500, poll_queue)
    root.after(500, poll_stop)
    root.mainloop()


def _gather_watch_processes(cfg_raw: dict) -> set[str]:
    watch: set[str] = set()
    apps = cfg_raw.get("application", [])
    for app in apps:
        for name in app.get("processes", []):
            if isinstance(name, str):
                watch.add(name.lower())
    return watch


def _format_applications(cfg_raw: dict) -> str:
    lines: list[str] = []
    for app in cfg_raw.get("application", []):
        app_id = app.get("id", "(unknown)")
        procs = app.get("processes", [])
        lines.append(f"- {app_id}: {', '.join(procs) if procs else '(no processes)'}")
    return "\n".join(lines) if lines else "(no applications)"


def _format_controllers(cfg_raw: dict) -> str:
    lines: list[str] = []
    for ctrl in cfg_raw.get("controllers", []):
        cid = ctrl.get("id", "(unknown)")
        host = ctrl.get("host", "")
        segs = ctrl.get("segments", [])
        lines.append(f"- {cid} @ {host} ({len(segs)} segments)")
    return "\n".join(lines) if lines else "(no controllers)"


def _lookup_controller(cfg_raw: dict, controller_id: str) -> dict | None:
    for ctrl in cfg_raw.get("controllers", []):
        if ctrl.get("id") == controller_id:
            return ctrl
    return None


def _lookup_mode(cfg_raw: dict, app_id: str, mode_id: str) -> dict | None:
    for app in cfg_raw.get("application", []):
        if app.get("id") != app_id:
            continue
        for mode in app.get("modes", []):
            if mode.get("id") == mode_id:
                return mode
    return None


def _apply_output(mode: dict, cfg_raw: dict, value: float, log_message):
    output_type = mode.get("output")
    color = mode.get("color", "#ffffff")
    transition = cfg_raw.get("default_transition_ms")

    if output_type == "fullfade":
        controller_id = mode.get("controller")
        ctrl = _lookup_controller(cfg_raw, controller_id) if controller_id else None
        if not ctrl:
            log_message(f"Controller {controller_id} not found")
            return
        host = ctrl.get("host")
        port = int(ctrl.get("port", 80))
        rlo = float(mode.get("rangelow", 0))
        rhi = float(mode.get("rangehigh", 100))
        if rhi <= rlo:
            log_message("Invalid range for mode")
            return
        pct = max(0.0, min(100.0, (value - rlo) / (rhi - rlo) * 100.0))
        try:
            wled.apply_fullfade(host=host, port=port, color_hex=color, health_pct=pct, transition_ms=transition)
            log_message(f"Applied {output_type} {value} -> {pct:.0f}% on {controller_id}")
        except Exception as exc:
            log_message(f"WLED error: {exc}")
    elif output_type == "segmentsolid":
        bindings = mode.get("bindings", {})
        if not isinstance(value, str):
            log_message("segmentsolid expects a binding name")
            return
        binding = bindings.get(value)
        if not binding:
            log_message(f"Binding '{value}' not found")
            return
        target_controller = binding.get("controller")
        if not target_controller:
            log_message("Binding missing controller")
            return
        acolor = mode.get("acolor", "#000000")
        bcolor = mode.get("bcolor", "#000000")
        try:
            abri = int(mode.get("abrightness", 0))
            bbri = int(mode.get("bbrightness", 0))
        except Exception:
            log_message("Invalid brightness values")
            return
        target_segment = binding.get("segment")
        controllers_filter = mode.get("controllers", [])
        transition_ms = mode.get("transition_ms", transition)
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
                log_message(f"WLED error on {cid}: {exc}")
        log_message(f"Applied segmentsolid '{value}'")
    else:
        log_message(f"Unsupported output type: {output_type}")


def _process_watch_loop(watch: set[str], stop_event: threading.Event, log_message):
    if not watch:
        return
    if psutil is None:
        log_message("Process watch unavailable (psutil not installed)")
        return
    seen: set[str] = set()
    try:
        while not stop_event.is_set():
            current: set[str] = set()
            try:
                for proc in psutil.process_iter(["name"]):
                    name = (proc.info.get("name") or "").lower()
                    if not name:
                        continue
                    if name in watch:
                        current.add(name)
                started = current - seen
                stopped = seen - current
                for name in sorted(started):
                    log_message(f"{name} started")
                for name in sorted(stopped):
                    log_message(f"{name} terminated")
                seen = current
            except Exception as exc:
                log_message(f"Process watch error: {exc}")
            time.sleep(1.0)
    except Exception as exc:
        log_message(f"Process watch stopped: {exc}")


def main() -> int:
    # Keep console logging minimal; main logging goes to the debug window queue.
    logging.basicConfig(level=logging.ERROR)
    stop_event = threading.Event()
    debug_request = threading.Event()
    log_queue: "queue.Queue[str]" = queue.Queue()
    log_buffer: list[str] = []
    cfg_raw: dict | None = None
    # expose cfg to debug window commands
    global cfg_raw_global
    cfg_raw_global = None

    def log_message(msg: str):
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        log_buffer.append(entry)
        try:
            log_queue.put(entry)
        except Exception:
            pass
    # Log config load once at startup.
    debug_on_start = False
    try:
        cfg = load_config(CONFIG_PATH)
        cfg_raw = cfg.raw
        cfg_raw_global = cfg.raw
        log_message(f"Config loaded: {cfg.path.resolve()}")
        debug_on_start = bool(cfg.raw.get("debug", False))
    except ConfigError as exc:
        log_message(f"Config error: {exc}")
        cfg = None

    # Single instance guard: if pipe already exists, exit.
    try:
        import win32file
        import win32pipe
        import pywintypes
    except Exception:
        log_message("Single-instance check skipped (pywin32 missing)")
    else:
        try:
            handle = win32file.CreateFile(
                SINGLE_INSTANCE_PIPE,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            # Pipe exists: another instance is running.
            log_message("Another RunLights instance is already running. Exiting.")
            return 0
        except pywintypes.error as exc:
            # If pipe not found, we'll create ours in serve_in_thread.
            if exc.winerror != 2:  # 2 = file not found
                log_message(f"Single-instance check error: {exc}")
        finally:
            try:
                handle.Close()
            except Exception:
                pass

    serve_in_thread(config_path=CONFIG_PATH, stop_event=stop_event, log_queue=log_queue)
    log_message(f"Tray IPC started on {PIPE_NAME}")

    tray_icon = start_tray_icon(stop_event, debug_request)

    # Start process watcher if we have processes configured.
    if cfg is not None:
        watch = _gather_watch_processes(cfg.raw)
        threading.Thread(
            target=_process_watch_loop,
            args=(watch, stop_event, log_message),
            daemon=True,
        ).start()

    if debug_on_start:
        debug_request.set()

    try:
        while not stop_event.is_set():
            if debug_request.is_set():
                debug_request.clear()
                threading.Thread(
                    target=_run_debug_window,
                    args=(stop_event, log_queue, log_buffer),
                    daemon=True,
                ).start()
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop_event.set()

    if tray_icon:
        tray_icon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
