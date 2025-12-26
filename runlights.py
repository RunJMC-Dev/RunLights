from __future__ import annotations

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

# Hard-coded icon path (use bundled icon.ico if present).
ICON_PATH = _here / "icon.ico"


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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()
    serve_in_thread(config_path=Path("config.toml"), stop_event=stop_event)
    logging.info("Tray IPC started on %s", PIPE_NAME)

    tray_icon = start_tray_icon(stop_event)

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

