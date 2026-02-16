from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
import ctypes
import re
import queue
from datetime import datetime
import math

# Allow running without installation by adjusting path.
_here = Path(__file__).resolve().parent
CONFIG_PATH = _here / "config.toml"
# Ensure relative paths (config/logo) work even when double-click launched.
os.chdir(_here)
sys.path.insert(0, str(_here / "src"))

PAUSE_EVENT = threading.Event()

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
    import pytesseract  # type: ignore
except Exception:
    pytesseract = None

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

# Hard-coded icon path (use bundled icon.ico if present).
ICON_PATH = _here / "icon.ico"
SINGLE_INSTANCE_PIPE = PIPE_NAME  # reuse the IPC pipe name for single-instance guard


def _show_message_box(title: str, message: str) -> None:
    """Best-effort Windows message box for critical errors."""
    try:
        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, title, MB_ICONERROR)
    except Exception:
        pass


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


class _DebugWindowState:
    """Simple holder so the main loop can manage the PySide6 debug UI."""

    def __init__(self, app, window):
        self.app = app
        self.window = window


def _windows_prefers_dark() -> bool:
    """Check Windows personalization setting for app theme."""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
    except Exception:
        return False


def _windows_accent_color() -> tuple[int, int, int] | None:
    """Read Windows accent color (ARGB DWORD) if available."""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            val, _ = winreg.QueryValueEx(key, "ColorizationColor")
            # ColorizationColor is ARGB (low byte = blue).
            a = (val >> 24) & 0xFF
            r = (val >> 16) & 0xFF
            g = (val >> 8) & 0xFF
            b = val & 0xFF
            if a == 0:  # unlikely, but avoid invisible accents
                a = 255
            return (r, g, b)
    except Exception:
        return None


def _apply_dark_palette(app):
    """Apply a dark Fusion palette, picking up Windows accent if available."""
    from PySide6 import QtGui, QtWidgets

    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    palette = QtGui.QPalette()
    bg = QtGui.QColor(32, 32, 32)
    alt = QtGui.QColor(45, 45, 45)
    text = QtGui.QColor(230, 230, 230)
    disabled = QtGui.QColor(140, 140, 140)
    accent_rgb = _windows_accent_color() or (0, 120, 215)  # Windows default accent-ish
    accent = QtGui.QColor(*accent_rgb)

    palette.setColor(QtGui.QPalette.Window, bg)
    palette.setColor(QtGui.QPalette.WindowText, text)
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(24, 24, 24))
    palette.setColor(QtGui.QPalette.AlternateBase, alt)
    palette.setColor(QtGui.QPalette.Text, text)
    palette.setColor(QtGui.QPalette.ToolTipBase, alt)
    palette.setColor(QtGui.QPalette.ToolTipText, text)
    palette.setColor(QtGui.QPalette.Button, alt)
    palette.setColor(QtGui.QPalette.ButtonText, text)
    palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor(255, 80, 80))
    palette.setColor(QtGui.QPalette.Highlight, accent)
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))

    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled)
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled)

    app.setPalette(palette)
    # Ensure text controls inherit dark bg
    app.setStyleSheet("QLineEdit, QPlainTextEdit { background: #1e1e1e; }")


def _prime_log_buffer(log_queue: "queue.Queue[str]", log_buffer: list[str]):
    """Drain any queued messages into the buffer before rendering the UI."""
    try:
        seen = set(log_buffer)
        while True:
            msg = log_queue.get_nowait()
            if msg not in seen:
                log_buffer.append(msg)
                seen.add(msg)
    except queue.Empty:
        pass


def _run_debug_window(
    stop_event: threading.Event,
    log_queue: "queue.Queue[str]",
    log_buffer: list[str],
    ocr_fail_queue: "queue.Queue[tuple[str, str]]",
):
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except Exception as exc:
        logging.warning("Cannot open debug window (PySide6 not available): %s", exc)
        return None

    _prime_log_buffer(log_queue, log_buffer)

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    try:
        if _windows_prefers_dark():
            _apply_dark_palette(app)
        elif "windowsvista" in QtWidgets.QStyleFactory.keys():
            app.setStyle("windowsvista")
    except Exception:
        pass

    class DebugWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("RunLights Debug")
            self.resize(1024, 512)
            self.overlay = None
            self._overlay_target = None
            try:
                self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
            except Exception:
                pass
            if ICON_PATH.exists():
                try:
                    self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
                except Exception:
                    pass

            central = QtWidgets.QWidget(self)
            root_layout = QtWidgets.QHBoxLayout(central)
            root_layout.setContentsMargins(10, 6, 10, 10)
            root_layout.setSpacing(8)

            sidebar = QtWidgets.QWidget(central)
            sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
            sidebar_layout.setContentsMargins(0, 0, 0, 0)
            sidebar_layout.setSpacing(6)
            sidebar.setFixedWidth(120)

            logo_label = QtWidgets.QLabel(sidebar)
            logo_label.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
            logo_path = _here / "images" / "logo.png"
            if logo_path.exists():
                try:
                    pixmap = QtGui.QPixmap(str(logo_path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaledToWidth(96, QtCore.Qt.SmoothTransformation)
                        logo_label.setPixmap(scaled)
                except Exception as exc:
                    logging.warning("Failed to load logo %s: %s", logo_path, exc)
            sidebar_layout.addWidget(logo_label)

            appconfig_btn = QtWidgets.QPushButton("App Config")
            appconfig_btn.clicked.connect(lambda _=None: self._show_app_config_dialog())
            sidebar_layout.addWidget(appconfig_btn)
            sidebar_layout.addStretch(1)

            main = QtWidgets.QWidget(central)
            layout = QtWidgets.QVBoxLayout(main)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            class _LogBox(QtWidgets.QPlainTextEdit):
                def __init__(self, parent):
                    super().__init__(parent)
                    self._focus_target = None

                def set_focus_target(self, target):
                    self._focus_target = target

                def mousePressEvent(self, event):  # type: ignore[override]
                    try:
                        if self._focus_target:
                            self._focus_target.setFocus()
                    except Exception:
                        pass
                    return super().mousePressEvent(event)

            self.log_box = _LogBox(central)
            self.log_box.setReadOnly(True)
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            font.setPointSize(10)
            self.log_box.setFont(font)
            self.log_box.setMinimumHeight(280)
            layout.addWidget(self.log_box, stretch=1)
            try:
                self.log_box.blockCountChanged.connect(lambda _=None: self._update_log_padding())
            except Exception:
                pass

            self.preview_overlay = QtWidgets.QLabel(self)
            self.preview_overlay.setVisible(False)
            self.preview_overlay.setAlignment(QtCore.Qt.AlignCenter)
            self.preview_overlay.setStyleSheet(
                "background: rgba(32,32,32,180); border: 1px solid rgba(200,200,200,120);"
            )

            class OverlayRect(QtWidgets.QWidget):
                def __init__(self):
                    super().__init__()
                    self.setWindowFlags(
                        QtCore.Qt.FramelessWindowHint
                        | QtCore.Qt.WindowStaysOnTopHint
                        | QtCore.Qt.Window
                        | QtCore.Qt.ToolTip
                    )
                    self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                    self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
                    self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
                    self.setFocusPolicy(QtCore.Qt.NoFocus)
                    try:
                        self.setWindowOpacity(0.8)
                    except Exception:
                        pass
                    try:
                        self.setWindowFlag(QtCore.Qt.NoDropShadowWindowHint, True)
                    except Exception:
                        pass

                def paintEvent(self, event):  # type: ignore[override]
                    painter = QtGui.QPainter(self)
                    painter.setRenderHint(QtGui.QPainter.Antialiasing)
                    pen = QtGui.QPen(QtGui.QColor(255, 0, 0, 255))
                    pen.setWidth(1)
                    painter.setPen(pen)
                    painter.setBrush(QtCore.Qt.NoBrush)
                    rect = self.rect().adjusted(0, 0, -1, -1)
                    painter.drawRect(rect)
                    painter.end()

            self.overlay_rect_cls = OverlayRect

            input_row = QtWidgets.QHBoxLayout()
            class HistoryLineEdit(QtWidgets.QLineEdit):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._history: list[str] = []
                    self._pos = 0

                def add_history(self, cmd: str):
                    if not cmd:
                        return
                    if not self._history or self._history[-1] != cmd:
                        self._history.append(cmd)
                    self._pos = len(self._history)

                def keyPressEvent(self, event):  # type: ignore[override]
                    key = event.key()
                    if key == QtCore.Qt.Key_Up:
                        if self._history:
                            self._pos = max(0, self._pos - 1)
                            self.setText(self._history[self._pos])
                            self.setCursorPosition(len(self.text()))
                        return
                    if key == QtCore.Qt.Key_Down:
                        if self._history:
                            self._pos = min(len(self._history), self._pos + 1)
                            if self._pos == len(self._history):
                                self.clear()
                            else:
                                self.setText(self._history[self._pos])
                                self.setCursorPosition(len(self.text()))
                        return
                    return super().keyPressEvent(event)

            self.input = HistoryLineEdit()
            self.input.setPlaceholderText("Enter command (showapplications, testoutput ...)")
            self._build_completer()
            self.send_btn = QtWidgets.QPushButton("Send")
            self.log_box.set_focus_target(self.input)
            input_row.addWidget(self.input, stretch=1)
            input_row.addWidget(self.send_btn)
            layout.addLayout(input_row)

            root_layout.addWidget(sidebar)
            root_layout.addWidget(main, stretch=1)
            self.setCentralWidget(central)

            # Start with some spacing at the top for readability.
            self.log_box.setPlainText("\n" * 10)

            # Replay the full log history when opening the window.
            for entry in log_buffer:
                self.append_line(entry, preformatted=True)
            self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

            self.input.returnPressed.connect(self._on_send)
            self.send_btn.clicked.connect(self._on_send)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(400)
            self._timer.timeout.connect(self._poll)
            self._timer.start()

        def _build_completer(self):
            ocr_completions: list[str] = []
            if cfg_raw_global:
                try:
                    for app in cfg_raw_global.get("application", []):
                        app_id = app.get("id", "")
                        for mode in app.get("modes", []):
                            if str(mode.get("input", "")).lower() != "screen_region":
                                continue
                            mode_id = mode.get("id", "")
                            if app_id and mode_id:
                                ocr_completions.append(f"ocrtest {app_id}.{mode_id}")
                except Exception:
                    ocr_completions = []

            commands = [
                "showapplications",
                "showcontrollers",
                "ocrtest",
                "ocroverlay ",
                "testoutput idle",
                "testoutput ",
                "loadpreset ",
                "getpreset ",
                "tasksearch ",
                "appconfig",
                "reloadconfig",
                "help",
                "?",
            ] + ocr_completions + [c.replace("ocrtest ", "ocroverlay ") for c in ocr_completions]
            try:
                completer = QtWidgets.QCompleter(commands, self)
                completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
                try:
                    completer.setFilterMode(QtCore.Qt.MatchContains)
                except Exception:
                    pass
                completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
                self.input.setCompleter(completer)
            except Exception:
                pass

        def closeEvent(self, event):  # type: ignore[override]
            try:
                self._timer.stop()
            except Exception:
                pass
            try:
                if self.overlay:
                    self.overlay.hide()
            except Exception:
                pass
            return super().closeEvent(event)

        def resizeEvent(self, event):  # type: ignore[override]
            self._position_overlays()
            self._update_log_padding()
            return super().resizeEvent(event)

        def _on_send(self):
            text = self.input.text()
            self.input.clear()
            if hasattr(self.input, "add_history"):
                try:
                    self.input.add_history(text)
                except Exception:
                    pass
            self.handle_command(text)

        def handle_command(self, cmd: str):
            raw_cmd = cmd.strip()
            cmd = raw_cmd.lower()
            if not cmd:
                return
            if cmd == "showapplications":
                result = _format_applications(cfg_raw_global) if cfg_raw_global else "(no config loaded)"
                self.append_line(result)
            elif cmd == "showcontrollers":
                result = _format_controllers(cfg_raw_global) if cfg_raw_global else "(no config loaded)"
                self.append_line(result)
            elif cmd in ("help", "?"):
                help_lines = [
                    "Debug commands:",
                    "  showapplications           - list configured apps/processes",
                    "  showcontrollers            - list controllers/segments",
                    "  ocrtest [app.mode] [delay] - run OCR once for a screen_region mode (optional delay sec)",
                    "  testoutput <app>.<mode> <value>",
                    "    fullfade:   testoutput myapp.health 42",
                    "    segmentsolid: testoutput esde.game-select arcade",
                    "  testoutput idle            - apply idle color/brightness",
                    "  loadpreset <controller> <preset> - apply a WLED preset by id or name",
                    "  getpreset <controller>     - show the current preset on a controller",
                    "  ocroverlay <app>.<mode>    - toggle green overlay on a screen_region",
                    "  tasksearch <term>          - list running tasks that contain term",
                    "  appconfig                  - open dialog to add/configure an application",
                    "  reloadconfig               - reload config.toml (threads keep old config)",
                ]
                self.append_line("\n".join(help_lines))
            elif cmd in ("appconfig", "addapp"):
                self._show_app_config_dialog()
            elif cmd.startswith("ocrtest"):
                if pytesseract is None:
                    self.append_line("OCR unavailable: pytesseract not installed")
                    return
                delay_s = 0
                target_mode = None
                parts = cmd.split()
                if len(parts) >= 2 and parts[1] != "ocr":
                    if "." in parts[1]:
                        app_id, mode_id = parts[1].split(".", 1)
                        target_mode = _lookup_mode(cfg_raw_global or {}, app_id, mode_id)
                        if not target_mode:
                            self.append_line(f"Mode {app_id}.{mode_id} not found")
                            return
                    if len(parts) >= 3:
                        try:
                            delay_s = max(0, int(parts[2]))
                        except Exception:
                            delay_s = 0
                if not target_mode:
                    ocr_modes = _collect_ocr_modes(cfg_raw_global or {})
                    if not ocr_modes:
                        self.append_line("No screen_region modes configured")
                        return
                    target = ocr_modes[0]
                    target_mode = target["mode"]
                    app_id = target["app_id"]
                    mode_id = target["mode_id"]
                    region = target["region"]
                else:
                    app_id = next((a.get("id") for a in cfg_raw_global.get("application", []) if target_mode in a.get("modes", [])), "(unknown)")
                    mode_id = target_mode.get("id", "(unknown)")
                    region = _mode_region(target_mode)
                if not region:
                    self.append_line(f"OCR region missing for {app_id}.{mode_id}")
                    return

                def run_ocr():
                    text, err, preview_img = _perform_ocr(region, include_image=True, mode=target_mode)
                    if err:
                        self.append_line(f"OCR {app_id}.{mode_id} error: {err}")
                        self.show_preview(None)
                        return
                    if not text:
                        self.append_line(f"OCR {app_id}.{mode_id}: (no text)")
                        self.show_preview(preview_img)
                        return
                    text = _apply_ocr_delimiter(target_mode, text, self.append_line)
                    if not text:
                        self.append_line(f"OCR {app_id}.{mode_id}: (empty after delimiter)")
                        self.show_preview(preview_img)
                        return
                    self.append_line(f"OCR {app_id}.{mode_id}: '{text}'")
                    self.show_preview(preview_img)
                    val: str | float = text
                    if target_mode.get("output") != "segmentsolid":
                        num = _extract_number(text)
                        num = _apply_input_range(target_mode, num, self.append_line)
                        if num is None:
                            self.append_line(f"OCR {app_id}.{mode_id}: non-numeric '{text}' ignored")
                            return
                        val = num
                    _apply_output(target_mode, cfg_raw_global or {}, val, self.append_line)

                if delay_s > 0:
                    def delayed():
                        for remaining in range(delay_s, 0, -1):
                            self.append_line(f"OCR {app_id}.{mode_id} in {remaining}s")
                            time.sleep(1)
                        run_ocr()
                    threading.Thread(target=delayed, daemon=True).start()
                else:
                    run_ocr()
            elif cmd.startswith("testoutput "):
                if not cfg_raw_global:
                    self.append_line("No config loaded")
                    return
                parts = cmd.split()
                if len(parts) != 3 or "." not in parts[1]:
                    self.append_line("Usage: testoutput <app>.<mode> <value>")
                    return
                app_mode = parts[1]
                raw_val = parts[2]
                app_id, mode_id = app_mode.split(".", 1)
                mode = _lookup_mode(cfg_raw_global, app_id, mode_id)
                if not mode:
                    self.append_line(f"Mode {app_id}.{mode_id} not found")
                    return
                if mode.get("output") == "segmentsolid":
                    val = raw_val
                else:
                    try:
                        val = float(raw_val)
                    except Exception:
                        self.append_line("Usage: testoutput <app>.<mode> <value>")
                        return
                _apply_output(mode, cfg_raw_global, val, self.append_line)
            elif cmd == "testoutput idle":
                if not cfg_raw_global:
                    self.append_line("No config loaded")
                    return
                idle_cfg = cfg_raw_global.get("idle")
                if not idle_cfg:
                    self.append_line("Idle not configured")
                    return
                color_hex = idle_cfg.get("color", "#000000")
                try:
                    bri = int(idle_cfg.get("brightness", 0))
                except Exception:
                    bri = 0
                transition_ms = idle_cfg.get("transition_ms", cfg_raw_global.get("default_transition_ms"))
                for ctrl_entry in cfg_raw_global.get("controllers", []):
                    chost = ctrl_entry.get("host")
                    cport = int(ctrl_entry.get("port", 80))
                    segments = ctrl_entry.get("segments", [])
                    if not segments:
                        continue
                    seg_updates = []
                    for seg in segments:
                        seg_id = seg.get("id")
                        seg_updates.append(
                            wled.WLEDPayload(
                                on=bri > 0,
                                brightness=bri,
                                color=wled._hex_to_rgb(color_hex),
                                segment=seg_id,
                            )
                        )
                    try:
                        wled.send_batch(
                            controller=wled.WLEDController(host=chost, port=cport),
                            seg_updates=seg_updates,
                            transition_ms=transition_ms,
                            timeout=_wled_timeout(cfg_raw_global),
                        )
                    except Exception as exc:
                        self.append_line(f"WLED error on {ctrl_entry.get('id')}: {exc}")
                self.append_line("Applied idle (all off)")
            elif cmd.startswith("loadpreset"):
                if not cfg_raw_global:
                    self.append_line("No config loaded")
                    return
                parts = cmd.split()
                if len(parts) < 3:
                    self.append_line("Usage: loadpreset <controller> <preset>")
                    return
                controller_id = parts[1]
                preset = " ".join(parts[2:])
                _apply_preset(controller_id, preset, cfg_raw_global, self.append_line)
            elif cmd.startswith("getpreset"):
                if not cfg_raw_global:
                    self.append_line("No config loaded")
                    return
                parts = cmd.split()
                if len(parts) != 2:
                    self.append_line("Usage: getpreset <controller>")
                    return
                controller_id = parts[1]
                _log_current_preset(controller_id, cfg_raw_global, self.append_line)
            elif cmd.startswith("ocroverlay"):
                if not cfg_raw_global:
                    self.append_line("No config loaded")
                    return
                parts = cmd.split()
                if len(parts) != 2 or "." not in parts[1]:
                    self.append_line("Usage: ocroverlay <app>.<mode>")
                    return
                app_id, mode_id = parts[1].split(".", 1)
                mode = _lookup_mode(cfg_raw_global, app_id, mode_id)
                if not mode:
                    self.append_line(f"Mode {app_id}.{mode_id} not found")
                    return
                if str(mode.get("input", "")).lower() != "screen_region":
                    self.append_line("Overlay only works for screen_region modes")
                    return
                region = _mode_region(mode)
                if not region:
                    self.append_line("Region not defined for that mode")
                    return
                target = f"{app_id}.{mode_id}"
                if self.overlay and self.overlay.isVisible() and self._overlay_target == target:
                    self.overlay.hide()
                    self._overlay_target = None
                    self.append_line("OCR overlay hidden")
                else:
                    if not self.overlay:
                        try:
                            self.overlay = self.overlay_rect_cls()
                        except Exception:
                            self.overlay = None
                    if not self.overlay:
                        self.append_line("Overlay unavailable")
                        return
                    x, y, w, h = region
                    scale = _screen_scale(QtWidgets.QApplication.instance())
                    if scale != 1.0:
                        lx = int(round(x / scale))
                        ly = int(round(y / scale))
                        lw = int(round(w / scale))
                        lh = int(round(h / scale))
                        self.append_line(f"OCR overlay scaled for DPI ({scale:.2f}): {lx},{ly},{lw},{lh}")
                        x, y, w, h = lx, ly, lw, lh
                    self.overlay.setGeometry(x, y, w, h)
                    self.overlay.show()
                    self.overlay.raise_()
                    self.overlay.repaint()
                    try:
                        self.overlay.activateWindow()
                    except Exception:
                        pass
                    self._overlay_target = target
                    self.append_line(f"OCR overlay shown for {target} at {region}")
            elif cmd.startswith("tasksearch"):
                if psutil is None:
                    self.append_line("Task search unavailable (psutil not installed)")
                    return
                term = raw_cmd[len("tasksearch"):].strip()
                if term.startswith(("\"", "'")) and term.endswith(("\"", "'")) and len(term) > 1:
                    term = term[1:-1]
                if not term:
                    self.append_line("Usage: tasksearch <term>")
                    return
                term_lower = term.lower()
                matches: set[str] = set()
                try:
                    for proc in psutil.process_iter(["name"]):
                        name = proc.info.get("name") or ""
                        if term_lower in name.lower():
                            matches.add(name)
                except Exception as exc:
                    self.append_line(f"Task search error: {exc}")
                    return
                if not matches:
                    self.append_line(f"No tasks found containing '{term}'")
                    return
                for name in sorted(matches):
                    self.append_line(name)
            elif cmd == "reloadconfig":
                new_cfg = _reload_config(self.append_line)
                if new_cfg is not None:
                    self._build_completer()
            else:
                self.append_line(f"Unknown command: {cmd}")

        def _show_app_config_dialog(self):
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("App Config")
            dialog.setModal(True)
            dialog.setMinimumWidth(700)
            dialog.resize(700, 520)
            PAUSE_EVENT.set()

            form = QtWidgets.QFormLayout(dialog)
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)

            def _section_label(text: str) -> QtWidgets.QLabel:
                label = QtWidgets.QLabel(text)
                label.setStyleSheet("font-weight: 600; padding-top: 6px;")
                return label

            def _section_line() -> QtWidgets.QFrame:
                line = QtWidgets.QFrame(dialog)
                line.setFrameShape(QtWidgets.QFrame.HLine)
                line.setFrameShadow(QtWidgets.QFrame.Sunken)
                return line

            app_id = QtWidgets.QLineEdit()
            app_id.setPlaceholderText("e.g. starrapture")
            friendly = QtWidgets.QLineEdit()
            friendly.setPlaceholderText("Optional display name")
            process = QtWidgets.QLineEdit()
            process.setPlaceholderText("e.g. Game.exe")

            conflict_label = QtWidgets.QLabel("")
            conflict_label.setStyleSheet("color: #c62828;")

            app_picker = QtWidgets.QComboBox()
            app_picker.addItem("New App", None)
            apps_by_id = {}
            friendly_to_id = {}
            if cfg_raw_global:
                for app in cfg_raw_global.get("application", []):
                    app_id_val = str(app.get("id", "")).strip()
                    if not app_id_val:
                        continue
                    apps_by_id[app_id_val] = app
                    fname = str(app.get("friendlyname", "")).strip()
                    if fname:
                        friendly_to_id[fname.lower()] = app_id_val
                    label = f"{app_id_val} — {fname}" if fname else app_id_val
                    app_picker.addItem(label, app_id_val)

            form.addRow(_section_label("Application"), QtWidgets.QLabel(""))
            form.addRow("Select app", app_picker)
            form.addRow("Id", app_id)
            form.addRow("Friendly name", friendly)
            form.addRow("Process", process)
            form.addRow(_section_line(), QtWidgets.QLabel(""))

            form.addRow(_section_label("Modes"), QtWidgets.QLabel(""))
            modes_wrap = QtWidgets.QWidget(dialog)
            modes_layout = QtWidgets.QVBoxLayout(modes_wrap)
            modes_layout.setContentsMargins(0, 0, 0, 0)
            modes_layout.setSpacing(6)
            form.addRow("", modes_wrap)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
            )
            form.addRow(buttons)
            # Status row (hidden unless needed)
            form.addRow("", conflict_label)
            conflict_label.setVisible(False)

            save_btn = buttons.button(QtWidgets.QDialogButtonBox.Ok)
            if save_btn:
                save_btn.setText("Save")

            existing_ids = set()
            if cfg_raw_global:
                for app in cfg_raw_global.get("application", []):
                    existing_ids.add(str(app.get("id", "")).lower())

            selected_app_id = {"value": None}
            current_modes: list[dict] = []
            initial_snapshot = {"value": None}

            def _snapshot_state():
                return {
                    "app_id": app_id.text().strip(),
                    "friendly": friendly.text().strip(),
                    "process": process.text().strip(),
                    "modes": [dict(m) for m in current_modes],
                }

            def _update_dirty():
                if save_btn is None:
                    return
                save_btn.setEnabled(_snapshot_state() != initial_snapshot["value"])

            def _load_modes_from_app(app):
                current_modes.clear()
                modes = app.get("modes", []) if app else []
                for m in modes:
                    if isinstance(m, dict):
                        current_modes.append(dict(m))

            def _refresh_modes_view():
                _clear_modes()
                modes = list(current_modes)
                if not modes:
                    label = QtWidgets.QLabel("No modes configured.")
                    label.setStyleSheet("color: #9aa0a6;")
                    modes_layout.addWidget(label)
                for mode in modes:
                    mode_id = str(mode.get("id", "")).strip() or "(unnamed)"
                    input_id = str(mode.get("input", "none")).strip() or "none"
                    output_id = str(mode.get("output", "none")).strip() or "none"
                    row = QtWidgets.QWidget(modes_wrap)
                    row_layout = QtWidgets.QHBoxLayout(row)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(8)
                    label = QtWidgets.QLabel(f"{mode_id}: {input_id} -> {output_id}")
                    label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
                    row_layout.addWidget(label)

                    edit_btn = QtWidgets.QToolButton(row)
                    edit_btn.setToolTip("Edit mode")
                    if not edit_icon.isNull():
                        edit_btn.setIcon(edit_icon)
                    else:
                        edit_btn.setText("Edit")

                    del_btn = QtWidgets.QToolButton(row)
                    del_btn.setToolTip("Delete mode")
                    if not delete_icon.isNull():
                        del_btn.setIcon(delete_icon)
                    else:
                        del_btn.setText("Delete")

                    row_layout.addWidget(edit_btn)
                    row_layout.addWidget(del_btn)
                    modes_layout.addWidget(row)
                    edit_btn.clicked.connect(lambda _=None, mid=mode_id: _edit_mode(mid))
                    del_btn.clicked.connect(lambda _=None, mid=mode_id: _delete_mode(mid))

                add_row = QtWidgets.QWidget(modes_wrap)
                add_layout = QtWidgets.QHBoxLayout(add_row)
                add_layout.setContentsMargins(0, 0, 0, 0)
                add_layout.setSpacing(8)
                add_layout.addStretch(1)
                add_btn = QtWidgets.QToolButton(add_row)
                add_btn.setToolTip("Add mode")
                add_btn.setText("Add")
                add_layout.addWidget(add_btn)
                modes_layout.addWidget(add_row)
                add_btn.clicked.connect(lambda _=None: _edit_mode(None))

            def _mode_dialog(existing: dict | None = None) -> dict | None:
                dlg = QtWidgets.QDialog(dialog)
                dlg.setWindowTitle("Mode")
                dlg.setModal(True)
                form = QtWidgets.QFormLayout(dlg)
                form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

                mode_id = QtWidgets.QLineEdit()
                mode_id.setPlaceholderText("e.g. health")
                input_mode = QtWidgets.QComboBox()
                input_mode.addItems(["None", "screen_region", "CLI"])
                output_mode = QtWidgets.QComboBox()
                output_mode.addItems(["None", "fullfade", "segmentsolid", "segmentpercent"])

                form.addRow("Mode id", mode_id)
                form.addRow("Input", input_mode)

                input_group = QtWidgets.QWidget(dlg)
                input_form = QtWidgets.QFormLayout(input_group)
                input_form.setContentsMargins(0, 0, 0, 0)
                input_x = QtWidgets.QLineEdit()
                input_y = QtWidgets.QLineEdit()
                input_w = QtWidgets.QLineEdit()
                input_h = QtWidgets.QLineEdit()
                input_min = QtWidgets.QLineEdit()
                input_max = QtWidgets.QLineEdit()
                for w in (input_x, input_y, input_w, input_h):
                    w.setPlaceholderText("0")
                input_form.addRow("X", input_x)
                input_form.addRow("Y", input_y)
                input_form.addRow("Width", input_w)
                input_form.addRow("Height", input_h)
                input_form.addRow("Input min", input_min)
                input_form.addRow("Input max", input_max)
                form.addRow("", input_group)

                form.addRow("Output", output_mode)
                test_wrap = QtWidgets.QWidget(dlg)
                test_layout = QtWidgets.QHBoxLayout(test_wrap)
                test_layout.setContentsMargins(0, 0, 0, 0)
                test_layout.setSpacing(8)
                test_toggle = QtWidgets.QCheckBox()
                test_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                test_slider.setRange(0, 100)
                test_slider.setValue(50)
                test_layout.addWidget(test_toggle)
                test_layout.addWidget(test_slider)
                form.addRow("Test", test_wrap)

                output_group = QtWidgets.QWidget(dlg)
                output_form = QtWidgets.QFormLayout(output_group)
                output_form.setContentsMargins(0, 0, 0, 0)
                controller_ids = []
                if cfg_raw_global:
                    for ctrl in cfg_raw_global.get("controllers", []):
                        cid = str(ctrl.get("id", "")).strip()
                        if cid:
                            controller_ids.append(cid)
                out_controllers = QtWidgets.QComboBox()
                out_controllers.addItem("")
                for cid in controller_ids:
                    out_controllers.addItem(cid)
                out_minvalue = QtWidgets.QLineEdit()
                out_maxvalue = QtWidgets.QLineEdit()
                out_acolor = QtWidgets.QLineEdit()
                out_bcolor = QtWidgets.QLineEdit()
                a_color_btn = QtWidgets.QToolButton()
                a_color_btn.setText("Pick")
                b_color_btn = QtWidgets.QToolButton()
                b_color_btn.setText("Pick")
                a_color_wrap = QtWidgets.QWidget(dlg)
                a_color_layout = QtWidgets.QHBoxLayout(a_color_wrap)
                a_color_layout.setContentsMargins(0, 0, 0, 0)
                a_color_layout.setSpacing(6)
                a_color_layout.addWidget(out_acolor)
                a_color_layout.addWidget(a_color_btn)
                b_color_wrap = QtWidgets.QWidget(dlg)
                b_color_layout = QtWidgets.QHBoxLayout(b_color_wrap)
                b_color_layout.setContentsMargins(0, 0, 0, 0)
                b_color_layout.setSpacing(6)
                b_color_layout.addWidget(out_bcolor)
                b_color_layout.addWidget(b_color_btn)

                out_abri = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                out_abri.setRange(0, 255)
                out_bbri = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                out_bbri.setRange(0, 255)
                a_bri_label = QtWidgets.QLabel("0")
                b_bri_label = QtWidgets.QLabel("0")
                a_bri_wrap = QtWidgets.QWidget(dlg)
                a_bri_layout = QtWidgets.QHBoxLayout(a_bri_wrap)
                a_bri_layout.setContentsMargins(0, 0, 0, 0)
                a_bri_layout.setSpacing(6)
                a_bri_layout.addWidget(out_abri, stretch=1)
                a_bri_layout.addWidget(a_bri_label)
                b_bri_wrap = QtWidgets.QWidget(dlg)
                b_bri_layout = QtWidgets.QHBoxLayout(b_bri_wrap)
                b_bri_layout.setContentsMargins(0, 0, 0, 0)
                b_bri_layout.setSpacing(6)
                b_bri_layout.addWidget(out_bbri, stretch=1)
                b_bri_layout.addWidget(b_bri_label)
                out_segment_reverse = QtWidgets.QCheckBox("Reverse segment order")
                output_form.addRow("Controllers", out_controllers)
                output_form.addRow("Min value", out_minvalue)
                output_form.addRow("Max value", out_maxvalue)
                output_form.addRow("A color", a_color_wrap)
                output_form.addRow("A brightness", a_bri_wrap)
                output_form.addRow("B color", b_color_wrap)
                output_form.addRow("B brightness", b_bri_wrap)
                output_form.addRow("", out_segment_reverse)
                form.addRow("", output_group)

                status = QtWidgets.QLabel("")
                status.setStyleSheet("color: #c62828;")
                status.setVisible(False)
                form.addRow("", status)

                buttons = QtWidgets.QDialogButtonBox(
                    QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
                )
                form.addRow(buttons)

                def _set_combo(widget, value, default="None"):
                    try:
                        widget.blockSignals(True)
                        if value:
                            idx = widget.findText(str(value))
                            widget.setCurrentIndex(idx if idx >= 0 else widget.findText(default))
                        else:
                            widget.setCurrentIndex(widget.findText(default))
                    finally:
                        widget.blockSignals(False)

                def _toggle_groups():
                    input_group.setVisible(input_mode.currentText() == "screen_region")
                    output_group.setVisible(output_mode.currentText() != "None")

                input_mode.currentIndexChanged.connect(_toggle_groups)
                output_mode.currentIndexChanged.connect(_toggle_groups)

                if existing:
                    mode_id.setText(str(existing.get("id", "")).strip())
                    _set_combo(input_mode, existing.get("input"))
                    _set_combo(output_mode, existing.get("output"))
                    input_x.setText(str(existing.get("x", "")).strip())
                    input_y.setText(str(existing.get("y", "")).strip())
                    input_w.setText(str(existing.get("width", "")).strip())
                    input_h.setText(str(existing.get("height", "")).strip())
                    input_min.setText(str(existing.get("inputrangemin", "")).strip())
                    input_max.setText(str(existing.get("inputrangemax", "")).strip())
                    existing_controllers = existing.get("controllers", []) or []
                    if existing_controllers:
                        idx = out_controllers.findText(str(existing_controllers[0]))
                        if idx >= 0:
                            out_controllers.setCurrentIndex(idx)
                    out_minvalue.setText(str(existing.get("minvalue", "")).strip())
                    out_maxvalue.setText(str(existing.get("maxvalue", "")).strip())
                    out_acolor.setText(str(existing.get("acolor", "")).strip())
                    try:
                        out_abri.setValue(int(existing.get("abrightness", 0)))
                    except Exception:
                        out_abri.setValue(0)
                    out_bcolor.setText(str(existing.get("bcolor", "")).strip())
                    try:
                        out_bbri.setValue(int(existing.get("bbrightness", 0)))
                    except Exception:
                        out_bbri.setValue(0)
                    out_segment_reverse.setChecked(bool(existing.get("segmentorderreverse", False)))

                _toggle_groups()

                def _collect_mode_for_test():
                    result = {"id": mode_id.text().strip() or "test"}
                    input_sel = input_mode.currentText()
                    output_sel = output_mode.currentText()
                    if input_sel != "None":
                        result["input"] = input_sel
                        if input_sel == "screen_region":
                            result["x"] = _to_int(input_x.text())
                            result["y"] = _to_int(input_y.text())
                            result["width"] = _to_int(input_w.text())
                            result["height"] = _to_int(input_h.text())
                            result["inputrangemin"] = _to_float(input_min.text())
                            result["inputrangemax"] = _to_float(input_max.text())
                    if output_sel != "None":
                        result["output"] = output_sel
                        controller_val = out_controllers.currentText().strip()
                        if controller_val:
                            result["controllers"] = [controller_val]
                        result["minvalue"] = _to_float(out_minvalue.text())
                        result["maxvalue"] = _to_float(out_maxvalue.text())
                        result["acolor"] = out_acolor.text().strip()
                        result["abrightness"] = out_abri.value()
                        result["bcolor"] = out_bcolor.text().strip()
                        result["bbrightness"] = out_bbri.value()
                        result["segmentorderreverse"] = out_segment_reverse.isChecked()
                    return result

                test_timer = QtCore.QTimer(dlg)
                test_timer.setInterval(1000)

                def _apply_test_output():
                    if not test_toggle.isChecked():
                        return
                    mode = _collect_mode_for_test()
                    output_sel = mode.get("output")
                    if not output_sel or output_sel == "None":
                        return
                    if output_sel == "segmentsolid":
                        status.setText("Test not supported for segmentsolid.")
                        status.setVisible(True)
                        return
                    try:
                        slider_pct = test_slider.value()
                        minv = mode.get("minvalue")
                        maxv = mode.get("maxvalue")
                        if minv is not None and maxv is not None:
                            val = float(minv) + (float(maxv) - float(minv)) * (slider_pct / 100.0)
                        else:
                            val = float(slider_pct)
                    except Exception:
                        return
                    _apply_output(mode, cfg_raw_global or {}, val, self.append_line)

                def _toggle_test():
                    if test_toggle.isChecked():
                        test_timer.start()
                        _apply_test_output()
                    else:
                        test_timer.stop()

                test_toggle.stateChanged.connect(lambda _=None: _toggle_test())
                test_slider.valueChanged.connect(lambda _=None: _apply_test_output())
                test_timer.timeout.connect(_apply_test_output)

                def _update_bri_labels():
                    a_bri_label.setText(str(out_abri.value()))
                    b_bri_label.setText(str(out_bbri.value()))

                out_abri.valueChanged.connect(lambda _=None: _update_bri_labels())
                out_bbri.valueChanged.connect(lambda _=None: _update_bri_labels())
                _update_bri_labels()

                def _pick_color(target: QtWidgets.QLineEdit):
                    color = QtWidgets.QColorDialog.getColor(
                        QtGui.QColor(target.text() or "#ffffff"), dlg, "Select Color"
                    )
                    if color.isValid():
                        target.setText(color.name())

                a_color_btn.clicked.connect(lambda _=None: _pick_color(out_acolor))
                b_color_btn.clicked.connect(lambda _=None: _pick_color(out_bcolor))

                def _to_int(text: str):
                    text = text.strip()
                    if text == "":
                        return None
                    try:
                        return int(text)
                    except Exception:
                        return None

                def _to_float(text: str):
                    text = text.strip()
                    if text == "":
                        return None
                    try:
                        return float(text)
                    except Exception:
                        return None

                result_holder = {"value": None}

                def _on_accept():
                    mid = mode_id.text().strip()
                    if not mid:
                        status.setText("Mode id is required.")
                        status.setVisible(True)
                        return
                    result = {"id": mid}
                    input_sel = input_mode.currentText()
                    output_sel = output_mode.currentText()
                    if input_sel != "None":
                        result["input"] = input_sel
                        if input_sel == "screen_region":
                            result["x"] = _to_int(input_x.text())
                            result["y"] = _to_int(input_y.text())
                            result["width"] = _to_int(input_w.text())
                            result["height"] = _to_int(input_h.text())
                            result["inputrangemin"] = _to_float(input_min.text())
                            result["inputrangemax"] = _to_float(input_max.text())
                    if output_sel != "None":
                        result["output"] = output_sel
                        controller_val = out_controllers.currentText().strip()
                        if controller_val:
                            result["controllers"] = [controller_val]
                        if output_sel == "fullfade":
                            result["minvalue"] = _to_float(out_minvalue.text())
                            result["maxvalue"] = _to_float(out_maxvalue.text())
                        if output_sel in ("segmentsolid", "segmentpercent"):
                            result["acolor"] = out_acolor.text().strip()
                            result["abrightness"] = out_abri.value()
                            result["bcolor"] = out_bcolor.text().strip()
                            result["bbrightness"] = out_bbri.value()
                        if output_sel == "segmentpercent":
                            result["minvalue"] = _to_float(out_minvalue.text())
                            result["maxvalue"] = _to_float(out_maxvalue.text())
                            result["segmentorderreverse"] = out_segment_reverse.isChecked()
                    result_holder["value"] = result
                    dlg.accept()

                buttons.accepted.connect(_on_accept)
                buttons.rejected.connect(dlg.reject)
                dlg.exec()
                return result_holder["value"]

            def _edit_mode(mode_id: str | None):
                existing = None
                if mode_id:
                    for m in current_modes:
                        if str(m.get("id", "")).strip() == mode_id:
                            existing = dict(m)
                            break
                new_mode = _mode_dialog(existing)
                if not new_mode:
                    return
                new_id = str(new_mode.get("id", "")).strip()
                existing_ids = {str(m.get("id", "")).strip().lower() for m in current_modes}
                if mode_id:
                    existing_ids.discard(mode_id.lower())
                if new_id.lower() in existing_ids:
                    QtWidgets.QMessageBox.warning(dialog, "Mode", "Mode id already exists.")
                    return
                if mode_id:
                    for idx, m in enumerate(current_modes):
                        if str(m.get("id", "")).strip() == mode_id:
                            current_modes[idx] = new_mode
                            break
                else:
                    current_modes.append(new_mode)
                _refresh_modes_view()
                _update_dirty()

            def _delete_mode(mode_id: str | None):
                if not mode_id:
                    return
                res = QtWidgets.QMessageBox.question(
                    dialog,
                    "Delete Mode",
                    f"Delete mode '{mode_id}'?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                )
                if res != QtWidgets.QMessageBox.Yes:
                    return
                current_modes[:] = [m for m in current_modes if str(m.get("id", "")).strip() != mode_id]
                _refresh_modes_view()
                _update_dirty()
            current_modes: list[dict] = []

            def _mdi_icon(name: str) -> QtGui.QIcon:
                svg_map = {
                    "edit": (
                        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"24\" height=\"24\" viewBox=\"0 0 24 24\">"
                        "<path fill=\"#cfd8dc\" d=\"M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zm2.92"
                        " 2.33H5v-.92l9.06-9.06.92.92-9.06 9.06zM20.71 7.04a1.003"
                        " 1.003 0 000-1.42l-2.34-2.34a1.003 1.003 0 00-1.42 0l-1.83"
                        " 1.83 3.75 3.75 1.84-1.82z\"/>"
                        "</svg>"
                    ),
                    "delete": (
                        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"24\" height=\"24\" viewBox=\"0 0 24 24\">"
                        "<path fill=\"#cfd8dc\" d=\"M9 3v1H4v2h16V4h-5V3H9m2 4v11h2V7h-2m-4"
                        " 0v11h2V7H7m8 0v11h2V7h-2z\"/>"
                        "</svg>"
                    ),
                }
                svg = svg_map.get(name)
                if not svg:
                    return QtGui.QIcon()
                try:
                    from PySide6 import QtSvg  # type: ignore

                    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
                    image = QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32)
                    image.fill(QtCore.Qt.transparent)
                    painter = QtGui.QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))
                except Exception:
                    return QtGui.QIcon()

            edit_icon = _mdi_icon("edit")
            delete_icon = _mdi_icon("delete")

            def _clear_modes():
                while modes_layout.count():
                    item = modes_layout.takeAt(0)
                    w = item.widget()
                    if w:
                        w.deleteLater()

            def _render_modes(app):
                _load_modes_from_app(app)
                _refresh_modes_view()

            def refresh_conflict():
                msg = ""
                app_id_val = app_id.text().strip().lower()
                friendly_val = friendly.text().strip().lower()
                is_new = app_picker.currentData() is None
                current_id = selected_app_id["value"]
                if app_id_val and app_id_val in existing_ids:
                    if is_new or (current_id and app_id_val != current_id.lower()):
                        msg = f"Id '{app_id_val}' already exists."
                if not msg and friendly_val:
                    owner = friendly_to_id.get(friendly_val)
                    if owner and (is_new or (current_id and owner != current_id)):
                        msg = f"Friendly name '{friendly.text().strip()}' already exists."
                conflict_label.setText(msg)
                conflict_label.setVisible(bool(msg))
                ok_btn = buttons.button(QtWidgets.QDialogButtonBox.Ok)
                if ok_btn:
                    ok_btn.setEnabled(bool(app_id.text().strip()) and bool(process.text().strip()) and not msg)
                _update_dirty()

            app_id.textChanged.connect(refresh_conflict)
            friendly.textChanged.connect(refresh_conflict)
            process.textChanged.connect(refresh_conflict)

            process_names: list[str] = []
            if psutil is not None:
                try:
                    seen: set[str] = set()
                    for proc in psutil.process_iter(["name"]):
                        name = (proc.info.get("name") or "").strip()
                        if not name:
                            continue
                        key = name.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        process_names.append(name)
                    process_names.sort(key=lambda v: v.lower())
                except Exception:
                    process_names = []

            process_popup = QtWidgets.QListWidget(dialog)
            process_popup.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
            process_popup.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            process_popup.setUniformItemSizes(True)
            process_popup.setFocusPolicy(QtCore.Qt.NoFocus)
            process_popup.setVisible(False)
            process_popup.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)

            def _position_process_popup():
                top_left = process.mapToGlobal(QtCore.QPoint(0, process.height()))
                width = max(260, process.width())
                process_popup.setGeometry(top_left.x(), top_left.y(), width, 180)

            def _refresh_process_popup():
                term = process.text().strip().lower()
                process_popup.clear()
                if not term or not process_names:
                    process_popup.setVisible(False)
                    return
                matches = [p for p in process_names if term in p.lower()]
                if not matches:
                    process_popup.setVisible(False)
                    return
                for name in matches[:100]:
                    process_popup.addItem(name)
                _position_process_popup()
                process_popup.setCurrentRow(0)
                process_popup.setVisible(True)
                process_popup.raise_()

            def _accept_process_selection():
                item = process_popup.currentItem()
                if item:
                    process.setText(item.text())
                process_popup.setVisible(False)

            def _dismiss_popup():
                process_popup.setVisible(False)

            def _on_process_text_changed():
                _refresh_process_popup()
                refresh_conflict()

            process.textChanged.connect(_on_process_text_changed)
            process_popup.itemClicked.connect(lambda _=None: _accept_process_selection())

            def _filter_event(obj, event):
                if obj is process and event.type() == QtCore.QEvent.KeyPress:
                    if event.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                        if process_popup.isVisible():
                            current = process_popup.currentRow()
                            if current < 0:
                                current = 0
                            if event.key() == QtCore.Qt.Key_Down:
                                current = min(process_popup.count() - 1, current + 1)
                            else:
                                current = max(0, current - 1)
                            process_popup.setCurrentRow(current)
                            return True
                    if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                        if process_popup.isVisible():
                            _accept_process_selection()
                            return True
                    if event.key() == QtCore.Qt.Key_Escape:
                        _dismiss_popup()
                        return True
                if obj is process_popup and event.type() == QtCore.QEvent.KeyPress:
                    if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                        _accept_process_selection()
                        return True
                    if event.key() == QtCore.Qt.Key_Escape:
                        _dismiss_popup()
                        return True
                if obj is dialog and event.type() == QtCore.QEvent.MouseButtonPress:
                    if process_popup.isVisible():
                        pos = event.globalPosition().toPoint()
                        if not process_popup.geometry().contains(pos) and not process.geometry().contains(process.mapFromGlobal(pos)):
                            _dismiss_popup()
                return False

            class _ProcessPopupFilter(QtCore.QObject):
                def eventFilter(self, obj, event):  # type: ignore[override]
                    return _filter_event(obj, event)

            popup_filter = _ProcessPopupFilter(dialog)
            process.installEventFilter(popup_filter)
            process_popup.installEventFilter(popup_filter)
            dialog.installEventFilter(popup_filter)

            def _set_text(widget, value):
                try:
                    widget.blockSignals(True)
                    widget.setText(value or "")
                finally:
                    widget.blockSignals(False)

            def _populate_from_app(app):
                _set_text(app_id, str(app.get("id", "")).strip())
                _set_text(friendly, str(app.get("friendlyname", "")).strip())
                procs = app.get("processes", []) or []
                _set_text(process, str(procs[0]).strip() if procs else "")
                selected_app_id["value"] = str(app.get("id", "")).strip() or None
                _dismiss_popup()
                _render_modes(app)
                refresh_conflict()
                initial_snapshot["value"] = _snapshot_state()
                _update_dirty()

            def _clear_fields():
                _set_text(app_id, "")
                _set_text(friendly, "")
                _set_text(process, "")
                selected_app_id["value"] = None
                _dismiss_popup()
                _render_modes(None)
                refresh_conflict()
                initial_snapshot["value"] = _snapshot_state()
                _update_dirty()

            def _on_app_picker_change():
                app_key = app_picker.currentData()
                if app_key is None:
                    _clear_fields()
                else:
                    app = apps_by_id.get(app_key)
                    if app:
                        _populate_from_app(app)

            app_picker.currentIndexChanged.connect(_on_app_picker_change)
            _on_app_picker_change()

            initial_snapshot["value"] = _snapshot_state()
            _update_dirty()

            def on_accept():
                app_id_val = app_id.text().strip()
                proc_name = process.text().strip()
                if not app_id_val:
                    QtWidgets.QMessageBox.warning(dialog, "Add Application", "Id is required.")
                    return
                if not proc_name:
                    QtWidgets.QMessageBox.warning(dialog, "Add Application", "Process is required.")
                    return
                if cfg_raw_global:
                    existing = {str(a.get("id", "")).lower() for a in cfg_raw_global.get("application", [])}
                    if app_id_val.lower() in existing and app_picker.currentData() is None:
                        QtWidgets.QMessageBox.warning(dialog, "Add Application", "That id already exists.")
                        return
                update_id = None if app_picker.currentData() is None else selected_app_id["value"]
                if _upsert_application_in_config(
                    CONFIG_PATH,
                    app_id_val,
                    [proc_name],
                    friendly.text(),
                    current_modes,
                    update_id,
                    self.append_line,
                ):
                    action = "Added" if app_picker.currentText() == "New App" else "Updated"
                    self.append_line(f"{action} application '{app_id_val}' in config.toml")
                    new_cfg = _reload_config(self.append_line)
                    if new_cfg is not None:
                        self._build_completer()
                    dialog.accept()

            buttons.accepted.connect(on_accept)
            buttons.rejected.connect(dialog.reject)
            app_id.setFocus()
            refresh_conflict()
            try:
                dialog.exec()
            finally:
                PAUSE_EVENT.clear()

        def append_line(self, line: str, preformatted: bool = False):
            if preformatted:
                text = line
            else:
                prefix = f"[{datetime.now().strftime('%H:%M:%S')}] "
                text = prefix + line
                if "\n" in text:
                    indent = " " * len(prefix)
                    text = text.replace("\n", "\n" + indent)
            self.log_box.appendPlainText(text)
            self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())
            self._update_log_padding()

        def show_preview(self, pil_image):
            try:
                if pil_image is None:
                    self.preview_overlay.setVisible(False)
                    return
                rgb = pil_image.convert("RGB")
                w, h = rgb.size
                data = rgb.tobytes("raw", "RGB")
                qimg = QtGui.QImage(data, w, h, 3 * w, QtGui.QImage.Format_RGB888)
                pixmap = QtGui.QPixmap.fromImage(qimg)
                pixmap = pixmap.scaled(260, 160, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                self.preview_overlay.setPixmap(pixmap)
                self.preview_overlay.resize(pixmap.size())
                self.preview_overlay.setVisible(True)
                self._position_overlays()
            except Exception:
                self.preview_overlay.setVisible(False)

        def _run_ocr_debug(self, app_id: str, mode_id: str):
            if pytesseract is None:
                self.append_line("OCR unavailable: pytesseract not installed")
                return
            cfg = cfg_raw_global or {}
            mode = _lookup_mode(cfg, app_id, mode_id)
            if not mode:
                self.append_line(f"Mode {app_id}.{mode_id} not found")
                return
            if str(mode.get("input", "")).lower() != "screen_region":
                self.append_line("OCR debug only supports screen_region modes")
                return
            region = _mode_region(mode)
            if not region:
                self.append_line(f"OCR region missing for {app_id}.{mode_id}")
                return
            text, err, preview_img = _perform_ocr(region, include_image=True)
            if err:
                self.append_line(f"OCR {app_id}.{mode_id} error: {err}")
                self.show_preview(preview_img)
                return
            if not text:
                self.append_line(f"OCR {app_id}.{mode_id}: (no text)")
                self.show_preview(preview_img)
                return
            text = _apply_ocr_delimiter(mode, text, self.append_line)
            if not text:
                self.append_line(f"OCR {app_id}.{mode_id}: (empty after delimiter)")
                self.show_preview(preview_img)
                return
            self.append_line(f"OCR {app_id}.{mode_id}: '{text}'")
            self.show_preview(preview_img)

        def _poll(self):
            if stop_event.is_set():
                self.close()
                return
            try:
                while True:
                    line = log_queue.get_nowait()
                    self.append_line(line, preformatted=True)
            except queue.Empty:
                pass
            try:
                while True:
                    app_id, mode_id = ocr_fail_queue.get_nowait()
                    self.append_line(
                        f"OCR failed; stopping. Showing ocrtest results for {app_id}.{mode_id}."
                    )
                    self._run_ocr_debug(app_id, mode_id)
            except queue.Empty:
                pass

        def _update_log_padding(self):
            """Align log text toward the bottom when few lines are present."""
            try:
                blocks = max(1, self.log_box.blockCount())
                line_h = self.log_box.fontMetrics().lineSpacing()
                content_h = blocks * line_h
                viewport_h = self.log_box.viewport().height()
                top_pad = max(0, viewport_h - content_h - 6)
                self.log_box.setViewportMargins(0, top_pad, 0, 0)
            except Exception:
                pass

        def _position_overlays(self):
            try:
                if self.preview_overlay and self.preview_overlay.isVisible():
                    w = self.preview_overlay.width()
                    x = max(8, self.width() - w - 12)
                    y = 10
                    self.preview_overlay.move(x, y)
                    self.preview_overlay.raise_()
            except Exception:
                pass

    window = DebugWindow()
    window.show()
    return _DebugWindowState(app, window)


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


def _lookup_controller_insensitive(cfg_raw: dict, controller_id: str) -> dict | None:
    target = controller_id.lower()
    for ctrl in cfg_raw.get("controllers", []):
        cid = str(ctrl.get("id", "")).lower()
        if cid == target:
            return ctrl
    return None


def _wled_timeout(cfg_raw: dict) -> float:
    """Return the configured WLED timeout (seconds), defaulting to 4 seconds."""
    try:
        return max(1.0, float(cfg_raw.get("wled_timeout_ms", 4000)) / 1000.0)
    except Exception:
        return 4.0


def _screen_scale(app=None):
    """Return the primary screen scale factor (device pixel ratio)."""
    try:
        if app:
            screen = app.primaryScreen()
            if screen:
                scale = screen.devicePixelRatio()
                if scale and scale > 0:
                    return float(scale)
                dpi = screen.logicalDotsPerInch()
                return max(1.0, dpi / 96.0)
        import ctypes  # type: ignore

        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, float(dpi) / 96.0)
    except Exception:
        return 1.0


def _reload_config(log_message):
    """Reload config from disk and update global reference."""
    global cfg_raw_global
    try:
        cfg = load_config(CONFIG_PATH)
        cfg_raw_global = cfg.raw
        log_message(f"Config reloaded: {cfg.path.resolve()}")
        log_message("Note: running threads use startup config; restart app to fully apply.")
        return cfg.raw
    except Exception as exc:
        log_message(f"Config reload failed: {exc}")
        return None


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\"", "\\\"")


def _render_application_block(
    app_id: str,
    processes: list[str],
    friendlyname: str | None,
    modes: list[dict] | None,
) -> list[str]:
    safe_id = _toml_escape(app_id)
    safe_name = _toml_escape(friendlyname.strip()) if friendlyname and friendlyname.strip() else ""
    proc_list = ", ".join(f"\"{_toml_escape(p)}\"" for p in processes)
    lines = ["", "[[application]]", f"id = \"{safe_id}\""]
    if safe_name:
        lines.append(f"friendlyname = \"{safe_name}\"")
    lines.append(f"processes = [{proc_list}]")
    if modes:
        for mode in modes:
            lines.append("")
            lines.append("  [[application.modes]]")
            mode_id = _toml_escape(str(mode.get("id", "default")))
            lines.append(f"  id = \"{mode_id}\"")
            input_mode = mode.get("input")
            if input_mode:
                lines.append(f"  input = \"{_toml_escape(str(input_mode))}\"")
            output_mode = mode.get("output")
            if output_mode:
                lines.append(f"  output = \"{_toml_escape(str(output_mode))}\"")
            for key, value in mode.items():
                if key in ("id", "input", "output"):
                    continue
                if value is None or value == "":
                    continue
                if isinstance(value, bool):
                    lines.append(f"  {key} = {str(value).lower()}")
                elif isinstance(value, (int, float)):
                    lines.append(f"  {key} = {value}")
                elif isinstance(value, list):
                    quoted = ", ".join(f"\"{_toml_escape(str(v))}\"" for v in value if str(v).strip())
                    lines.append(f"  {key} = [{quoted}]")
                else:
                    lines.append(f"  {key} = \"{_toml_escape(str(value))}\"")
    lines.append("")
    return lines


def _find_application_block(lines: list[str], target_id: str) -> tuple[int, int] | None:
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == "[[application]]":
            start = idx
            end = len(lines)
            for j in range(idx + 1, len(lines)):
                if lines[j].strip() == "[[application]]":
                    end = j
                    break
            block = lines[idx:end]
            for bl in block:
                m = re.match(r'^\s*id\s*=\s*"([^"]+)"\s*$', bl)
                if m and m.group(1) == target_id:
                    return idx, end
            start = None
    return None


def _upsert_application_in_config(
    config_path: Path,
    app_id: str,
    processes: list[str],
    friendlyname: str | None,
    modes: list[dict] | None,
    update_id: str | None,
    log_message,
) -> bool:
    if not config_path.exists():
        log_message(f"Config file not found: {config_path}")
        return False
    app_id = app_id.strip()
    if not app_id:
        log_message("App id is required")
        return False
    processes = [p.strip() for p in processes if p.strip()]
    if not processes:
        log_message("At least one process name is required")
        return False
    if cfg_raw_global:
        existing = {str(a.get("id", "")).lower() for a in cfg_raw_global.get("application", [])}
        if app_id.lower() in existing:
            log_message(f"Application '{app_id}' already exists")
            return False
    block_lines = _render_application_block(app_id, processes, friendlyname, modes)
    try:
        raw = config_path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        if update_id:
            found = _find_application_block(lines, update_id)
            if found:
                start, end = found
                lines[start:end] = block_lines[1:]  # drop leading blank for replacement
            else:
                lines.extend(block_lines)
        else:
            lines.extend(block_lines)
        out = "\n".join(lines)
        if raw.endswith("\n"):
            out += "\n"
        config_path.write_text(out, encoding="utf-8")
        return True
    except Exception as exc:
        log_message(f"Failed to write config: {exc}")
        return False


def _lookup_mode(cfg_raw: dict, app_id: str, mode_id: str) -> dict | None:
    for app in cfg_raw.get("application", []):
        if app.get("id") != app_id:
            continue
        for mode in app.get("modes", []):
            if mode.get("id") == mode_id:
                return mode
    return None


def _mode_region(mode: dict) -> tuple[int, int, int, int] | None:
    try:
        x = int(mode.get("x"))
        y = int(mode.get("y"))
        w = int(mode.get("width"))
        h = int(mode.get("height"))
    except Exception:
        return None
    return (x, y, w, h)

def _apply_ocr_delimiter(mode: dict, text: str, log_message=None) -> str:
    """If mode has delimiter, keep text to the left of the delimiter."""
    delim = mode.get("delimiter")
    if not delim:
        return text
    if delim in text:
        trimmed = text.split(delim, 1)[0]
        return trimmed
    return text


def _apply_output(mode: dict, cfg_raw: dict, value: float, log_message):
    output_type = mode.get("output")
    color = mode.get("color", "#ffffff")
    transition = cfg_raw.get("default_transition_ms")

    if output_type == "fullfade":
        controllers_filter = list(mode.get("controllers", []) or [])
        legacy_controller = mode.get("controller")
        if legacy_controller and not controllers_filter:
            controllers_filter = [legacy_controller]
        if not controllers_filter:
            log_message("fullfade missing controllers")
            return
        try:
            minv = float(mode.get("minvalue", 0))
            maxv = float(mode.get("maxvalue", 100))
        except Exception:
            minv, maxv = 0.0, 100.0
        try:
            val_f = float(value)
        except Exception:
            log_message("Invalid numeric value for fullfade")
            return
        if val_f < minv or val_f > maxv:
            log_message(f"Value {val_f} outside [{minv}, {maxv}] ignored")
            return
        span = max(1e-9, maxv - minv)
        pct = max(0.0, min(100.0, (val_f - minv) / span * 100.0))
        for controller_id in controllers_filter:
            ctrl = _lookup_controller(cfg_raw, controller_id) if controller_id else None
            if not ctrl:
                log_message(f"Controller {controller_id} not found")
                continue
            host = ctrl.get("host")
            port = int(ctrl.get("port", 80))
            try:
                wled.apply_fullfade(
                    host=host,
                    port=port,
                    color_hex=color,
                    health_pct=pct,
                    transition_ms=transition,
                    timeout=_wled_timeout(cfg_raw),
                )
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
        target_controllers = list(binding.get("controllers", []) or [])
        legacy_controller = binding.get("controller")
        if legacy_controller and not target_controllers:
            target_controllers = [legacy_controller]
        if not target_controllers:
            log_message("Binding missing controllers")
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
                is_target = cid in target_controllers and seg_id == target_segment
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
                    timeout=_wled_timeout(cfg_raw),
                )
            except Exception as exc:
                log_message(f"WLED error on {cid}: {exc}")
        log_message(f"Applied segmentsolid '{value}'")
    elif output_type == "segmentpercent":
        controllers_filter = mode.get("controllers", [])
        acolor = mode.get("acolor", "#000000")
        bcolor = mode.get("bcolor", "#000000")
        try:
            abri = int(mode.get("abrightness", 0))
            bbri = int(mode.get("bbrightness", 0))
        except Exception:
            log_message("Invalid brightness values")
            return
        try:
            val_f = float(value)
        except Exception:
            log_message("segmentpercent expects a numeric value")
            return
        try:
            minv = float(mode.get("minvalue", 0))
            maxv = float(mode.get("maxvalue", 100))
        except Exception:
            minv, maxv = 0.0, 100.0
        if val_f < minv or val_f > maxv:
            log_message(f"Value {val_f} outside [{minv}, {maxv}] ignored")
            return
        span = max(1e-9, maxv - minv)
        pct = max(0.0, min(100.0, (val_f - minv) / span * 100.0))

        for ctrl_entry in cfg_raw.get("controllers", []):
            cid = ctrl_entry.get("id")
            if controllers_filter and cid not in controllers_filter:
                continue
            chost = ctrl_entry.get("host")
            cport = int(ctrl_entry.get("port", 80))
            segments = ctrl_entry.get("segments", [])
            if not segments:
                continue
            if mode.get("sgementorderreverse") or mode.get("segmentorderreverse"):
                segments = list(reversed(segments))
            # Use the order defined in config.
            total = len(segments)
            if pct <= 0:
                filled = 0
            else:
                filled = min(total, max(1, int(math.ceil((pct / 100.0) * total))))

            seg_updates = []
            for idx, seg in enumerate(segments):
                seg_id = seg.get("id")
                is_filled = idx < filled
                seg_color = acolor if is_filled else bcolor
                seg_bri = abri if is_filled else bbri
                seg_updates.append(
                    wled.WLEDPayload(
                        on=seg_bri > 0,
                        brightness=seg_bri,
                        color=wled._hex_to_rgb(seg_color),
                        segment=seg_id,
                    )
                )
            try:
                wled.send_batch(
                    controller=wled.WLEDController(host=chost, port=cport),
                    seg_updates=seg_updates,
                    transition_ms=transition,
                    timeout=_wled_timeout(cfg_raw),
                )
            except Exception as exc:
                log_message(f"WLED error on {cid}: {exc}")
        log_message(f"Applied segmentpercent {pct:.0f}% from value {val_f}")
    else:
        log_message(f"Unsupported output type: {output_type}")


def _apply_segmentsolid_base(mode: dict, cfg_raw: dict, log_message):
    bindings = mode.get("bindings", {})
    controllers_filter = mode.get("controllers", [])
    acolor = mode.get("acolor", "#000000")
    bcolor = mode.get("bcolor", "#000000")
    try:
        abri = int(mode.get("abrightness", 0))
        bbri = int(mode.get("bbrightness", 0))
    except Exception:
        log_message("Invalid brightness values")
        return
    transition_ms = mode.get("transition_ms", cfg_raw.get("default_transition_ms"))
    # Base state: no target; all segments get B.
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
            seg_updates.append(
                wled.WLEDPayload(
                    on=bbri > 0,
                    brightness=bbri,
                    color=wled._hex_to_rgb(bcolor),
                    segment=seg_id,
                )
            )
        try:
            wled.send_batch(
                controller=wled.WLEDController(host=chost, port=cport),
                seg_updates=seg_updates,
                transition_ms=transition_ms,
                timeout=_wled_timeout(cfg_raw),
            )
        except Exception as exc:
            log_message(f"WLED error on {cid}: {exc}")
    log_message("Applied segmentsolid base state")


def _apply_preset(controller_id: str, preset: str, cfg_raw: dict, log_message):
    ctrl_entry = _lookup_controller_insensitive(cfg_raw, controller_id)
    if not ctrl_entry:
        log_message(f"Controller {controller_id} not found")
        return
    chost = ctrl_entry.get("host")
    cport = int(ctrl_entry.get("port", 80))
    transition_ms = cfg_raw.get("default_transition_ms")
    try:
        wled.apply_preset(
            controller=wled.WLEDController(host=chost, port=cport),
            preset=preset,
            transition_ms=transition_ms,
            timeout=_wled_timeout(cfg_raw),
        )
        log_message(f"Applied preset '{preset}' on {ctrl_entry.get('id', controller_id)}")
    except Exception as exc:
        log_message(f"WLED error on {ctrl_entry.get('id', controller_id)}: {exc}")


def _log_current_preset(controller_id: str, cfg_raw: dict, log_message):
    """Fetch and log the current preset (id and name if available) for a controller."""
    ctrl_entry = _lookup_controller_insensitive(cfg_raw, controller_id)
    if not ctrl_entry:
        log_message(f"Controller {controller_id} not found")
        return
    cid = ctrl_entry.get("id", controller_id)
    ctrl = wled.WLEDController(
        host=ctrl_entry.get("host"),
        port=int(ctrl_entry.get("port", 80)),
    )
    try:
        pid = wled.current_preset_id(ctrl, timeout=_wled_timeout(cfg_raw))
    except Exception as exc:
        log_message(f"WLED error on {cid}: {exc}")
        return
    if pid is None:
        log_message(f"{cid}: no active preset")
        return
    preset_name = None
    try:
        presets = wled.fetch_presets(ctrl, timeout=_wled_timeout(cfg_raw))
        entry = presets.get(str(pid))
        if isinstance(entry, dict):
            preset_name = entry.get("n")
        elif isinstance(entry, str):
            preset_name = entry
    except Exception:
        preset_name = None
    if preset_name:
        log_message(f"{cid}: preset {pid} ('{preset_name}')")
    else:
        log_message(f"{cid}: preset {pid}")


def _apply_global_gaming_preset(cfg_raw: dict, preset_name: str, log_message) -> dict[str, int | None]:
    """
    Snapshot current presets for all controllers and apply a gaming preset.
    Returns a map of controller id -> previous preset id (or None).
    """
    stored: dict[str, int | None] = {}
    transition_ms = cfg_raw.get("default_transition_ms")
    for ctrl_entry in cfg_raw.get("controllers", []):
        cid = ctrl_entry.get("id")
        chost = ctrl_entry.get("host")
        cport = int(ctrl_entry.get("port", 80))
        ctrl = wled.WLEDController(host=chost, port=cport)
        prev_pid = None
        ctrl_preset = ctrl_entry.get("gaming_preset", preset_name)
        try:
            prev_pid = wled.current_preset_id(ctrl, timeout=_wled_timeout(cfg_raw))
        except Exception as exc:
            log_message(f"Preset snapshot error on {cid}: {exc}")
        stored[cid] = prev_pid
        try:
            wled.apply_preset(controller=ctrl, preset=ctrl_preset, transition_ms=transition_ms, timeout=_wled_timeout(cfg_raw))
        except Exception as exc:
            log_message(f"WLED gaming preset error on {cid}: {exc}")
    log_message(f"Applied gaming preset '{preset_name}' to {len(stored)} controller(s)")
    return stored


def _restore_presets(cfg_raw: dict, stored_presets: dict[str, int | None], log_message):
    """Restore presets previously captured by _apply_global_gaming_preset."""
    if not stored_presets:
        return
    transition_ms = cfg_raw.get("default_transition_ms")
    for ctrl_entry in cfg_raw.get("controllers", []):
        cid = ctrl_entry.get("id")
        prev_pid = stored_presets.get(cid)
        if prev_pid is None:
            continue
        chost = ctrl_entry.get("host")
        cport = int(ctrl_entry.get("port", 80))
        ctrl = wled.WLEDController(host=chost, port=cport)
        log_message(f"Restoring preset {prev_pid} on {cid}")
        try:
            wled.apply_preset(controller=ctrl, preset=prev_pid, transition_ms=transition_ms, timeout=_wled_timeout(cfg_raw))
        except Exception as exc:
            log_message(f"WLED restore error on {cid}: {exc}")
    stored_presets.clear()
    log_message("Restored previous presets")


def _apply_idle(cfg_raw: dict, log_message):
    idle_cfg = cfg_raw.get("idle")
    if not idle_cfg:
        return
    color_hex = idle_cfg.get("color", "#000000")
    try:
        bri = int(idle_cfg.get("brightness", 0))
    except Exception:
        bri = 0
    transition_ms = idle_cfg.get("transition_ms", cfg_raw.get("default_transition_ms"))
    for ctrl_entry in cfg_raw.get("controllers", []):
        chost = ctrl_entry.get("host")
        cport = int(ctrl_entry.get("port", 80))
        segments = ctrl_entry.get("segments", [])
        if not segments:
            continue
        seg_updates = []
        for seg in segments:
            seg_id = seg.get("id")
            seg_updates.append(
                wled.WLEDPayload(
                    on=bri > 0,
                    brightness=bri,
                    color=wled._hex_to_rgb(color_hex),
                    segment=seg_id,
                )
            )
        try:
            wled.send_batch(
                controller=wled.WLEDController(host=chost, port=cport),
                seg_updates=seg_updates,
                transition_ms=transition_ms,
                timeout=_wled_timeout(cfg_raw),
            )
        except Exception as exc:
            log_message(f"WLED error on {ctrl_entry.get('id')}: {exc}")
    log_message("Applied idle state")


def _process_watch_loop(cfg_raw: dict, stop_event: threading.Event, log_message):
    watch: dict[str, str] = {}
    for app in cfg_raw.get("application", []):
        for name in app.get("processes", []):
            if isinstance(name, str):
                watch[name.lower()] = app.get("id")
    if not watch:
        return
    if psutil is None:
        log_message("Process watch unavailable (psutil not installed)")
        return
    seen: set[str] = set()
    active_app: str | None = None
    stored_presets: dict[str, int | None] = {}
    gaming_preset = cfg_raw.get("gaming_preset", "Gaming")
    try:
        while not stop_event.is_set():
            if PAUSE_EVENT.is_set():
                time.sleep(0.2)
                continue
            current: set[str] = set()
            try:
                for proc in psutil.process_iter(["name"]):
                    name = (proc.info.get("name") or "").lower()
                    if not name:
                        continue
                    if name in watch:
                        current.add(watch[name])
                started_apps = current - seen
                stopped_apps = seen - current
                for app_id in sorted(started_apps):
                    log_message(f"{app_id} started")
                for app_id in sorted(stopped_apps):
                    log_message(f"{app_id} terminated")
                # Switch active app
                if started_apps:
                    active_app = sorted(started_apps)[0]
                    if not stored_presets:
                        stored_presets = _apply_global_gaming_preset(cfg_raw, gaming_preset, log_message)
                    # Apply base output for first mode
                    modes = next((a.get("modes", []) for a in cfg_raw.get("application", []) if a.get("id") == active_app), [])
                    if modes:
                        mode = modes[0]
                        if mode.get("output") == "segmentsolid":
                            _apply_segmentsolid_base(mode, cfg_raw, log_message)
                elif stopped_apps:
                    if active_app in stopped_apps or not current:
                        active_app = None
                        _restore_presets(cfg_raw, stored_presets, log_message)
                        if cfg_raw.get("idle"):
                            _apply_idle(cfg_raw, log_message)
                seen = current
            except Exception as exc:
                log_message(f"Process watch error: {exc}")
            time.sleep(1.0)
    except Exception as exc:
        log_message(f"Process watch stopped: {exc}")


def _extract_number(text: str) -> float | None:
    """Try to pull a number out of OCR text."""
    m = re.search(r"[-+]?\d*\.?\d+", text)
    if not m:
        return None
    try:
        return float(m.group())
    except Exception:
        return None


def _apply_input_range(mode: dict, value: float | None, log_message=None) -> float | None:
    """Normalize input to 0-100 when inputrangemin/max are set."""
    if value is None:
        return None
    try:
        min_v = mode.get("inputrangemin")
        max_v = mode.get("inputrangemax")
        min_f = float(min_v) if min_v is not None and str(min_v).strip() != "" else None
        max_f = float(max_v) if max_v is not None and str(max_v).strip() != "" else None
        if min_f is None or max_f is None:
            return value
        if max_f <= min_f:
            return value
        pct = (value - min_f) / (max_f - min_f) * 100.0
        return max(0.0, min(100.0, pct))
    except Exception:
        return value


def _perform_ocr(
    region: tuple[int, int, int, int],
    include_image: bool = False,
    mode: dict | None = None,
):
    """Capture screen region and run OCR. Returns (text, error, image?)."""
    if pytesseract is None:
        return None, "pytesseract not available", None
    try:
        from PIL import ImageEnhance, ImageGrab, ImageOps  # type: ignore
    except Exception as exc:
        return None, f"Pillow ImageGrab unavailable: {exc}", None

    x, y, w, h = region
    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    except Exception as exc:
        return None, f"Capture failed: {exc}", None
    try:
        ocr_threshold = 140
        ocr_contrast = 1.0
        ocr_invert = False
        ocr_color = None
        ocr_color_tolerance = 80
        if mode:
            try:
                ocr_threshold = int(mode.get("ocr_threshold", ocr_threshold))
            except Exception:
                ocr_threshold = 140
            try:
                ocr_contrast = float(mode.get("ocr_contrast", ocr_contrast))
            except Exception:
                ocr_contrast = 1.0
            ocr_invert = bool(mode.get("ocr_invert", False))
            ocr_color = mode.get("ocr_color")
            try:
                ocr_color_tolerance = int(mode.get("ocr_color_tolerance", ocr_color_tolerance))
            except Exception:
                ocr_color_tolerance = 80

        gray = None
        if ocr_color:
            color_name = str(ocr_color).strip().lower()
            color_map = {
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255),
                "yellow": (255, 255, 0),
                "cyan": (0, 255, 255),
                "magenta": (255, 0, 255),
                "white": (255, 255, 255),
                "black": (0, 0, 0),
            }
            try:
                target = color_map.get(color_name) or wled._hex_to_rgb(color_name)
            except Exception as exc:
                return None, f"Invalid ocr_color '{ocr_color}': {exc}", None
            try:
                tol = max(0, min(255, int(ocr_color_tolerance)))
            except Exception:
                tol = 80
            rgb = img.convert("RGB")
            mask_data = []
            tr, tg, tb = target
            for r, g, b in rgb.getdata():
                if max(abs(r - tr), abs(g - tg), abs(b - tb)) <= tol:
                    mask_data.append(255)
                else:
                    mask_data.append(0)
            gray = ImageOps.autocontrast(ImageOps.grayscale(rgb))
            gray.putdata(mask_data)
        if gray is None:
            gray = img.convert("L")

        if ocr_contrast and abs(ocr_contrast - 1.0) > 0.01:
            try:
                gray = ImageEnhance.Contrast(gray).enhance(max(0.1, ocr_contrast))
            except Exception:
                pass
        if ocr_invert:
            try:
                gray = ImageOps.invert(gray)
            except Exception:
                pass
        # Light binarization to help OCR on tinted overlays/backgrounds.
        thresh = gray.point(lambda p: 255 if p > ocr_threshold else 0)
        text = pytesseract.image_to_string(thresh, config="--psm 6")
        return text.strip(), None, (img if include_image else None)
    except Exception as exc:
        return None, f"OCR failed: {exc}", None


def _collect_ocr_modes(cfg_raw: dict):
    modes: list[dict] = []
    for app in cfg_raw.get("application", []):
        processes = [p.lower() for p in app.get("processes", []) if isinstance(p, str)]
        for mode in app.get("modes", []):
            if str(mode.get("input", "")).lower() != "screen_region":
                continue
            region = _mode_region(mode)
            if not region:
                continue
            try:
                interval_ms = int(mode.get("interval_ms", mode.get("sample_ms", 1000)))
            except Exception:
                interval_ms = 1000
            modes.append(
                {
                    "app_id": app.get("id", "(unknown)"),
                    "mode_id": mode.get("id", "(unknown)"),
                    "mode": mode,
                    "processes": processes,
                    "region": region,
                    "interval": max(50, interval_ms) / 1000.0,
                    "last_text": None,
                    "next": 0.0,
                }
            )
    return modes


def _process_running(targets: list[str]) -> bool:
    if not targets:
        return True
    if psutil is None:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in targets:
                return True
    except Exception:
        return False
    return False


def _ocr_poll_loop(
    cfg_raw: dict,
    stop_event: threading.Event,
    log_message,
    ocr_fail_queue: "queue.Queue[tuple[str, str]] | None" = None,
):
    entries = _collect_ocr_modes(cfg_raw)
    if not entries:
        return
    if pytesseract is None:
        log_message("OCR disabled: pytesseract not available")
        return
    if psutil is None:
        log_message("OCR disabled: psutil not installed (process detection)")
        return
    log_message(f"OCR active for {len(entries)} mode(s)")
    try:
        while not stop_event.is_set():
            if PAUSE_EVENT.is_set():
                time.sleep(0.2)
                continue
            now = time.monotonic()
            for entry in entries:
                if now < entry["next"]:
                    continue
                entry["next"] = now + entry["interval"]
                if not _process_running(entry["processes"]):
                    continue
                text, err, _ = _perform_ocr(entry["region"], mode=entry["mode"])
                if err:
                    log_message(f"OCR {entry['app_id']}.{entry['mode_id']}: {err}")
                    if ocr_fail_queue is not None:
                        try:
                            ocr_fail_queue.put((entry["app_id"], entry["mode_id"]))
                        except Exception:
                            pass
                    log_message("OCR stopped after error")
                    return
                if not text:
                    continue
                text = _apply_ocr_delimiter(entry["mode"], text, log_message)
                if not text:
                    continue
                if text == entry["last_text"]:
                    continue
                entry["last_text"] = text
                log_message(f"OCR {entry['app_id']}.{entry['mode_id']}: '{text}'")
                val = text
                if entry["mode"].get("output") != "segmentsolid":
                    num = _extract_number(text)
                    num = _apply_input_range(entry["mode"], num, log_message)
                    if num is not None:
                        val = num
                    else:
                        log_message(f"OCR {entry['app_id']}.{entry['mode_id']}: non-numeric '{text}' ignored")
                        if ocr_fail_queue is not None:
                            try:
                                ocr_fail_queue.put((entry["app_id"], entry["mode_id"]))
                            except Exception:
                                pass
                        log_message("OCR stopped after non-numeric result")
                        return
                _apply_output(entry["mode"], cfg_raw, val, log_message)
            time.sleep(0.05)
    except Exception as exc:
        log_message(f"OCR loop stopped: {exc}")


def main() -> int:
    # Keep console logging minimal; main logging goes to the debug window queue.
    logging.basicConfig(level=logging.ERROR)
    stop_event = threading.Event()
    debug_request = threading.Event()
    log_queue: "queue.Queue[str]" = queue.Queue()
    log_buffer: list[str] = []
    ocr_fail_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
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
        try:
            _show_message_box("RunLights config error", str(exc))
        except Exception:
            pass
        cfg = None

    if cfg_raw is not None and cfg_raw.get("idle"):
        _apply_idle(cfg_raw, log_message)

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
    debug_ui: _DebugWindowState | None = None

    # Start process watcher if we have processes configured.
    if cfg is not None:
        threading.Thread(
            target=_process_watch_loop,
            args=(cfg.raw, stop_event, log_message),
            daemon=True,
        ).start()
        threading.Thread(
            target=_ocr_poll_loop,
            args=(cfg.raw, stop_event, log_message, ocr_fail_queue),
            daemon=True,
        ).start()

    if debug_on_start:
        debug_request.set()

    try:
        while not stop_event.is_set():
            if debug_request.is_set():
                debug_request.clear()
                try:
                    debug_ui = _run_debug_window(stop_event, log_queue, log_buffer, ocr_fail_queue)
                except Exception as exc:
                    log_message(f"Debug window failed: {exc}")
                    debug_ui = None
            if debug_ui:
                try:
                    if debug_ui.window and not debug_ui.window.isVisible():
                        debug_ui = None
                    else:
                        debug_ui.app.processEvents()
                except Exception:
                    debug_ui = None
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop_event.set()

    if tray_icon:
        tray_icon.stop()
    if debug_ui and debug_ui.window:
        try:
            debug_ui.window.close()
            debug_ui.app.processEvents()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
