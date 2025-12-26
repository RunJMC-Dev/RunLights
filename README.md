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
- Copy `config.example.toml` to `%APPDATA%/RunLights/config.toml` and edit.
- Uses REST with transitions; default update interval is `500ms` and can be tweaked per config.
- Define controllers (static IPs), segments, and modes keyed off process names.
- Modes can have startup presets (with transitions), steady states, shortcuts, and optional screen-region input parameters (for games like Quake).

## CLI example (planned)
```
runlights --host 192.168.1.50 segments set --name "shelf" --effect "highlight" --label "megadrive"
```
Replace the host/segment/effect labels to match your WLED setup.

## Roadmap
- Decide on Python version and dependency set.
- Define config format for multiple controllers/segments.
- Add GUI tray app with quick actions.
- Implement CLI parity with GUI actions.
- Packaging/distribution (installer? pip package?).

## License
TBD.
