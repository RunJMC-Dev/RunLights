from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RunLights tray/IPC server")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="Path to config.toml")
    parser.add_argument("--debug", action="store_true", help="Open a simple debug window")
    parser.add_argument("--no-tray", action="store_true", help="Run without a tray icon")
    return parser


def start_tray_icon(stop_event: threading.Event) -> pystray.Icon | None:
    if pystray is None:
        logging.warning("pystray/Pillow not installed; tray icon disabled")
        return None

    icon_image = _load_icon_image()
    if icon_image is None:
        logging.warning("No icon available; tray icon disabled")
        return None

    def on_quit(icon, item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(pystray.MenuItem("Quit", on_quit))
    icon = pystray.Icon("RunLights", icon_image, "RunLights", menu=menu)
    icon.run_detached()
    return icon


def _load_icon_image():
    # Try bundled icon.ico first.
    ico_path = _here / "icon.ico"
    try:
        if ico_path.exists():
            return Image.open(ico_path)
    except Exception:
        pass
    # Fallback: simple blue square.
    try:
        img = Image.new("RGB", (64, 64), (0, 100, 220))
        draw = ImageDraw.Draw(img)
        draw.rectangle([16, 16, 48, 48], fill=(255, 255, 255))
        return img
    except Exception:
        return None


def start_debug_window(stop_event: threading.Event):
    try:
        import tkinter as tk
    except Exception as exc:
        logging.warning("Cannot open debug window (tkinter not available): %s", exc)
        return

    root = tk.Tk()
    root.title("RunLights Debug")
    root.geometry("320x200")

    tk.Label(root, text="RunLights debug view (placeholder)", font=("Segoe UI", 11)).pack(pady=8)
    tk.Label(root, text=f"IPC pipe: {PIPE_NAME}", font=("Segoe UI", 9)).pack(pady=4)

    def on_close():
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)

    stop_event = threading.Event()
    serve_in_thread(config_path=args.config, stop_event=stop_event)
    logging.info("Tray IPC started on %s", PIPE_NAME)

    tray_icon = None
    if not args.no_tray:
        tray_icon = start_tray_icon(stop_event)

    if args.debug:
        # Run debug window in main thread; it will set stop_event on close.
        start_debug_window(stop_event)
    else:
        try:
            while not stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            stop_event.set()

    if tray_icon:
        tray_icon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

