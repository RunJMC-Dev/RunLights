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

    def append_line(line: str, preformatted: bool = False):
        text = line if preformatted else f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.configure(state="disabled")
        log_box.see("end")

    # preload existing buffer
    for entry in log_buffer:
        append_line(entry, preformatted=True)

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
        log_message(f"Config loaded: {cfg.path.resolve()}")
        debug_on_start = bool(cfg.raw.get("debug", False))
    except ConfigError as exc:
        log_message(f"Config error: {exc}")
        cfg = None

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
