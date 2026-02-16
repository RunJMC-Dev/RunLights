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
- Modes are keyed off process names; can include screen-region inputs, range mapping, and outputs such as `fullfade` (whole strip brightness from range) and `segmentsolid` (target segment vs others with A/B colors/brightness).
- `idle` block defines color/brightness/transition when idle or when watched apps close.
- ESDE bindings: map console names to controller/segment pairs under `application.modes."game-select".bindings` for `segmentsolid`.

## Mode Inputs/Outputs
Inputs
- `screen_region`: OCR a screen region and use the parsed value in a mode (optional `inputrangemin`/`inputrangemax`).
- `CLI`: input is provided by the named-pipe CLI (used for ES-DE console selection).

Outputs
- `fullfade`: map a numeric value to overall brightness/color on one or more controllers (uses `minvalue`/`maxvalue`).
- `segmentsolid`: apply A/B colors to segments, highlighting a bound target segment.
- `segmentpercent`: fill a percentage of segments based on a numeric value.

## ESDE integration
- Minimal standalone helper: `python standalone_cli.py <console>` (or place alongside ES-DE scripts; it reads `argv[3]` too). It sends the console name over the named pipe; if the tray isn’t running it no-ops without crashing ES-DE.
- Restart tray: `python standalone_cli.py restart` (best-effort; requires `psutil` to target the correct process).
- Only `/scripts/game-select` is needed; process detection handles startup/quit.

## Debug window commands
- `showapplications` / `showcontrollers`
- `testoutput <app>.<mode> <value>`: drives outputs via config (`fullfade` uses range; `segmentsolid` uses bindings A/B).
- `testoutput idle`: apply idle color/brightness to all segments.
- `loadpreset <controller> <preset>`: apply a WLED preset by id or name.
- `getpreset <controller>`: show the current preset on a controller.
- `ocroverlay <app>.<mode>`: toggle a green overlay on a screen_region mode.
- `tasksearch <term>`: list running tasks that contain term.
- `appconfig`: opens a dialog to add/configure an `[[application]]` entry (includes a process picker and optional input/output mode settings).
- `reloadconfig`: reload config.toml (threads keep old config).

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
