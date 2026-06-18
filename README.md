# RunLights

WLED interface for PC applications that can drive multiple WLED instances from a desktop GUI (system tray) or a CLI.

## Features
- Control multiple WLED controllers from one PC.
- Target specific segments (e.g., highlight the shelf with the Mega Drive).
- System tray GUI for quick toggles plus a CLI for scripting/automation.
- Designed to plug into other PC applications.

## Interfaces
- **GUI**: Runs as a tray app for quick on/off, segment selection, and presets.
- **CLI**: Mirrors core actions for scripting/automation.

## Quick start
1. Clone: `git clone https://github.com/RunJMC-Dev/RunLights.git`
2. Create/activate a virtual environment.
3. Install deps: `pip install -r requirements.txt` (pywin32, psutil, requests, pystray, Pillow, PySide6, pytesseract). Requires Tesseract installed on Windows for OCR.
4. Copy `config.example.toml` to `config.toml` in the app folder and edit.
5. Launch tray (no console): double-click `runlights.pyw` (or `pythonw runlights.pyw`). It applies the idle state on start and watches configured processes.

## Configuration (TOML)
- Copy `config.example.toml` to `config.toml` in the app folder (keep it beside the app so it moves with it) and edit.
- Uses WLED REST with transitions; default update interval is `500ms` and can be tweaked per config.
- Controllers use an `id` for references (no spaces) plus an optional human-friendly `name`; define static IPs and segments.
- Controllers can define `gaming_preset` (applied on game start) and `idle_preset` (applied on launch, game exit, and app exit).
- Modes are keyed off process names; can include screen-region inputs, range mapping, and outputs such as `crossfade` (whole strip brightness from range) and `segmentsolid` (target segment vs others with A/B colors/brightness).
- `idle` block defines color/brightness/transition when idle or when watched apps close.
- ESDE bindings: map console names to controller/segment pairs under `application.modes."game-select".bindings` for `segmentsolid`.
- `notification` block controls the `notify` debug overlay (duration seconds, font, fontsize, fontcolour, padding, bodycolour, bodyopacity 0-100, border px, timestamp on/off, align topcenter).
- `debug_window` block controls Debug Messages filters (output/input booleans) plus log mirroring (log_to_notifications).
- `mqtt` block enables Home Assistant notifications over MQTT; it subscribes to a topic and expects JSON with a `message` field (everything else ignored).
- For `screen_region` inputs, set `interval_ms` on a mode to control OCR refresh rate (default 1000ms).
- Optional in-game gate for `screen_region` modes: add an `ingame` block to only run OCR when a marker is visible.
  - `type = "color"`: match a UI color in a small region (cheap, no OCR).
  - `type = "text"`: OCR a small region and match `text` or `regex`.
  - Gate options: `x/y/width/height`, `interval_ms`, `hold_ms`, `tolerance`, `min_percent`, `sample_step`.
- Outputs support `dangertype = "flash"` with `dangerthreshold` (numeric, compared to input value).


Example: in-game gate for a health OCR mode
```toml
[[application.modes]]
id = "health"
input = "screen_region"
output = "crossfade"
x = 140
y = 458
width = 100
height = 200
controllers = ["PCROOMLHS"]

  [application.modes.ingame]
  type = "color"
  x = 120
  y = 420
  width = 40
  height = 20
  color = "#ffffff"
  tolerance = 30
  min_percent = 2
  interval_ms = 300
  hold_ms = 1500
```

## Mode Inputs/Outputs
Inputs
- `screen_region`: OCR a screen region and use the parsed value in a mode (optional `inputrangemin`/`inputrangemax`).
- `CLI`: input is provided by the named-pipe CLI (used for ES-DE console selection).

Outputs
- `crossfade`:
  - Technical description: apply the input value (0-100) as a brightness of all segments on the defined controllers.
  - Dialog description: Fade from A to B. (tip set A/B brightness to 0 to fade to/from black)
  - Settings:
    - min brightness: WLED min brightness (range 1-255)
    - max brightness: WLED max brightness (range 1-255)
    - acolour: colour at 0
    - abrightness: brightness at 0
    - bcolour: colour at 100
    - bbrightness: brightness at 100
- `fade`:
  - Technical description: apply the input value (0-100) as a brightness of all segments on the defined controllers.
  - Dialog description: Single colour fade from brightness A to brightness B.
  - Settings:
    - min brightness: WLED min brightness (range 1-255)
    - max brightness: WLED max brightness (range 1-255)
    - acolour: colour
    - abrightness: brightness at 0
    - bbrightness: brightness at 100
- `segmentsolid`: apply A/B colors to segments, highlighting a bound target segment.
- `segmentpercent`: fill a percentage of segments based on a numeric value.

## ESDE integration
- Minimal standalone helper: `python standalone_cli.py <console>` (or place alongside ES-DE scripts; it reads `argv[3]` too). It sends the console name over the named pipe; if the tray isn’t running it no-ops without crashing ES-DE.
- Restart tray: `python standalone_cli.py restart` (best-effort; requires `psutil` to target the correct process).
- Only `/scripts/game-select` is needed; process detection handles startup/quit.
- On Windows, use a small `game-select.bat` in the ES-DE event folder that launches a `.pyw` helper with `%*`. This preserves ES-DE's event arguments while avoiding a visible Python console window. Keep the `.pyw` helper outside the event folder itself (for example in a `helpers` subfolder) so ES-DE does not launch it directly without arguments.
- The live helper writes diagnostics to `D:\Emulators\EmulationStation\ES-DE\scripts\game-select\helpers\runlights-game-select.log`; if a shelf does not light up, check whether ES-DE passed a blank system name or a name that is missing from `config.toml` bindings.

## Debug window commands
- `showapplications` / `showcontrollers`
- `testoutput <app>.<mode> <value>`: drives outputs via config (`crossfade` uses range; `segmentsolid` uses bindings A/B).
- `testoutput idle`: apply idle color/brightness to all segments.
- `loadpreset <controller> <preset>`: apply a WLED preset by id or name.
- `getpreset <controller>`: show the current preset on a controller.
- `ocroverlay <app>.<mode>`: toggle a green overlay on a screen_region mode.
- `notify <message>`: show a top-center text overlay for 10s.
- `tasksearch <term>`: list running tasks that contain term.
- `appconfig`: opens a dialog to add/configure an `[[application]]` entry (includes a process picker and optional input/output mode settings).
- `notification`: use the Debug window sidebar button to edit notify overlay settings (includes a test button and font picker).
- `settings`: use the Debug window sidebar **Settings** button to toggle **Start on Boot** (writes/removes a registry entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
- Debug window checkbox "Output log to on screen notifications." mirrors log lines through the shared on-screen notification overlay.
- Debug window sidebar includes "Debug Messages" Output/Input filters to hide those log lines.
- `reloadconfig`: reload config.toml (threads keep old config).
- Mode editor test output is automatically stopped when the dialog closes.
- Mode editor test output applies `gaming_preset` on start and `idle_preset` on stop/close.
- Debug log lines are classified as Output/Input/Other for filters and on-screen notifications.
- Mode editor exposes danger flash settings (type + threshold) and uses them for test output.

## Tray IPC (Windows)
- IPC uses a Windows named pipe: `\\.\pipe\runlights_ipc` (requires `pywin32`).
- Run the tray: `runlights.pyw` (reads `config.toml` from the working directory); no command-line arguments are used. This will later be packaged as an auto-starting exe.
- Tray icon: uses a bundled `icon.ico` in the app folder (hard-coded fallback shape if missing); requires `pystray` and `Pillow`.
- The CLI connects to the pipe and sends a JSON message: `{"type":"console","name":"<your console>"}`.

## Roadmap
- Decide on Python version and dependency set.
- Define config format for multiple controllers/segments.
- Add GUI tray app with quick actions.
- Implement CLI parity with GUI actions.
- Packaging/distribution (installer? pip package?).
- Context-aware behavior: detect foreground window/app focus and adapt lighting based on the active app.

## License
TBD.
