from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
import queue

# Allow running without installation by adjusting path.
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here / "src"))

from runlights.tray import serve_in_thread  # noqa: E402
from runlights.ipc import PIPE_NAME  # noqa: E402

try:
    import pystray  # type: ignore
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pystray = None

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


def _run_debug_window(stop_event: threading.Event, log_queue: "queue.Queue[str]"):
    try:
        import tkinter as tk
        from tkinter import scrolledtext
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            Image = None  # type: ignore
            ImageTk = None  # type: ignore
    except Exception as exc:
        logging.warning("Cannot open debug window (tkinter not available): %s", exc)
        return

    root = tk.Tk()
    root.title("RunLights Debug")
    root.geometry("320x200")

    if Image and ImageTk:
        logo_path = _here / "images" / "logo.png"
        if logo_path.exists():
            try:
                img = Image.open(logo_path)
                img = img.resize((128, 128), Image.ANTIALIAS)
                photo = ImageTk.PhotoImage(img)
                logo_label = tk.Label(root, image=photo)
                logo_label.image = photo  # keep reference
                logo_label.pack(pady=4)
            except Exception:
                pass

    tk.Label(root, text="RunLights debug view", font=("Segoe UI", 11)).pack(pady=4)
    tk.Label(root, text=f"IPC pipe: {PIPE_NAME}", font=("Segoe UI", 9)).pack(pady=2)

    log_box = scrolledtext.ScrolledText(root, width=40, height=6, state="disabled", font=("Consolas", 9))
    log_box.pack(padx=8, pady=6, fill="both", expand=True)

    def append_line(line: str):
        log_box.configure(state="normal")
        log_box.insert("end", line + "\n")
        log_box.configure(state="disabled")
        log_box.see("end")

    append_line("Waiting for CLI messages...")

    def poll_queue():
        try:
            while True:
                line = log_queue.get_nowait()
                append_line(line)
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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()
    debug_request = threading.Event()
    log_queue: "queue.Queue[str]" = queue.Queue()
    serve_in_thread(config_path=Path("config.toml"), stop_event=stop_event, log_queue=log_queue)
    logging.info("Tray IPC started on %s", PIPE_NAME)

    tray_icon = start_tray_icon(stop_event, debug_request)

    try:
        while not stop_event.is_set():
            if debug_request.is_set():
                debug_request.clear()
                _run_debug_window(stop_event, log_queue)
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop_event.set()

    if tray_icon:
        tray_icon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
