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
_Setup details are TBD; fill in once Python version and packaging are chosen (pip/Poetry, venv, etc.)._
1. Clone: `git clone https://github.com/RunJMC-Dev/RunLights.git`
2. Create/activate a virtual environment (recommended).
3. Install dependencies once defined (e.g., `pip install -r requirements.txt`).
4. Run the GUI or CLI entrypoint (to be added).

## Configuration (TOML)
- Copy `config.example.toml` to `config.toml` in the app folder (keep it beside the app so it moves with it) and edit.
- Uses REST with transitions; default update interval is `500ms` and can be tweaked per config.
- Controllers use an `id` for references (no spaces) plus an optional human-friendly `name`; define static IPs and segments.
- Modes are keyed off process names; can include startup presets, steady states, shortcuts, and optional screen-region input parameters (for games like Quake).
- ESDE bindings: map console names to controller/segment pairs under `application.modes."game-select".bindings` so `runlights-cli console <name>` can hand off to the tray.

## CLI example (planned)
```
runlights --host 192.168.1.50 segments set --name "shelf" --effect "highlight" --label "megadrive"
```
Replace the host/segment/effect labels to match your WLED setup.

## ESDE integration (planned)
- ESDE will call a minimal CLI: `python runlights_cli.py <name>` (e.g., `python runlights_cli.py snes`).
- The CLI hands the console name to the tray process via IPC (Windows named pipe); the tray resolves it using the ESDE bindings in `config.toml` and applies actions/presets with transitions.
- Only `/scripts/game-select` is needed; `/scripts/startup` and `/scripts/quit` can be dropped because process detection will handle ESDE lifecycle.

## Current CLI scaffold
- Entry point: `python runlights_cli.py <name>` (runs without installing; uses local `src/`).
- Behavior today: forwards the console name to the tray via IPC. If the tray isn’t running, it exits non-zero with a warning.
- Next step: implement WLED apply path and direct-apply fallback; the tray resolves the name using config bindings.

## Tray IPC (Windows)
- IPC uses a Windows named pipe: `\\.\pipe\runlights_ipc` (requires `pywin32`).
- Run the tray server: `python runlights_tray.py` (reads `config.toml` from the working directory).
- The CLI connects to that pipe and sends a JSON message: `{"type":"console","name":"<your console>"}`.

## Roadmap
- Decide on Python version and dependency set.
- Define config format for multiple controllers/segments.
- Add GUI tray app with quick actions.
- Implement CLI parity with GUI actions.
- Packaging/distribution (installer? pip package?).

## License
TBD.
