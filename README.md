# RunLights

WLED interface for PC applications that can drive multiple WLED instances from a desktop GUI (system tray) or a CLI.

## Features

- Control multiple WLED controllers from one PC.
- Target specific segments (e.g., highlight the shelf with the Mega Drive).
- System tray GUI for quick toggles plus a CLI for scripting/automation.
- Designed to plug into other PC applications.
- Modular lifecycle and application-context events for isolated snap-in modules.
- Optional device-specific TV remote diagnostics and Stremio gamepad profile.

## Interfaces
- **GUI**: Runs as a tray app for quick on/off, segment selection, and presets.
- **CLI**: Mirrors core actions for scripting/automation.

## Quick start
1. Clone: `git clone https://github.com/RunJMC-Dev/RunLights.git`
2. Create/activate a virtual environment.
3. Install deps: `pip install -r requirements.txt` (pywin32, psutil, requests, pystray, Pillow, PySide6, pytesseract). Requires Tesseract installed on Windows for OCR.
4. Copy `config.example.toml` to `config.toml` in the app folder and edit.
5. Launch tray (no console): double-click `runlights.pyw` (or `pythonw runlights.pyw`). The launcher supports both direct execution and Windows file associations that invoke it through Python's module runner. It applies the idle state on start and watches configured processes.

## Configuration (TOML)
- Copy `config.example.toml` to `config.toml` in the app folder (keep it beside the app so it moves with it) and edit.
- Uses WLED REST with transitions; default update interval is `500ms` and can be tweaked per config.
- Controllers use an `id` for references (no spaces) plus an optional human-friendly `name`; define static IPs and segments.
- Controllers can define `gaming_preset` (applied on game start) and `idle_preset` (applied on launch, game exit, and app exit).
- Modes are keyed off process names; can include screen-region inputs, range mapping, and outputs such as `crossfade` (whole strip brightness from range) and `segmentsolid` (target segment vs others with A/B colors/brightness).
- `idle` block defines color/brightness/transition when idle or when watched apps close.
- ESDE bindings: map console names to controller/segment pairs under `application.modes."game-select".bindings` for `segmentsolid`.
- `notification` block controls the `notify` debug overlay (duration seconds, font, fontsize, fontcolour, padding, bodycolour, bodyopacity 0-100, border px, timestamp on/off, align topcenter).
- `overlays.clock` controls the clock overlay; `show_playtime = true` adds an elapsed play-time line while a watched app is active, resetting when the active app changes and hiding when idle.
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

### Remote Bridge (G20S / Stremio)

`Runlights.RemoteBridge` is a separate companion process supervised by RunLights. A bridge crash does not run inside the lighting/UI process, and RunLights restarts it with a short delay. It reads Windows Raw Input from only the configured physical receiver (`VID_4842`, `PID_0001` by default), never records ordinary keyboard text, and activates an application profile only while its executable is foreground.

Open **Debug → Remote** to enable/disable the companion, select the device definition and VID/PID, inspect matching interfaces, and watch the live input/profile/action log. The safe default is `input_mode = "observe"` with the `diagnostic` backend; it observes and tests mappings but does not inject or suppress input. Configuration is data-driven under `[remote_bridge]`, `[[remote_bridge.devices]]`, and `[[remote_bridge.profiles]]`; see `config.example.toml` for the complete Stremio profile. A future FreeTube profile can use the same structure with `executables = ["FreeTube.exe"]` and its own bindings.

Profiles may also set `default = true` with no executables. That fallback is selected whenever no application-specific profile matches, so a permanently owned remote can retain normal desktop/media behavior. A matching application profile always wins. The `sendinput` backend accepts named actions such as `key_up`, `key_enter`, `key_escape`, `key_media_play_pause`, letters/digits, `key_f1` through `key_f24`, and chords such as `key_shift+tab` or `key_ctrl+l`. For safety, RunLights refuses this backend unless `input_mode = "owned"`; observational Raw Input would otherwise duplicate the receiver's original HID keystrokes.

Example fallback and per-app key profiles after ownership is installed:

```toml
[[remote_bridge.profiles]]
id = "stock"
default = true
backend = "sendinput"
  [remote_bridge.profiles.bindings]
  up = "key_up"
  down = "key_down"
  left = "key_left"
  right = "key_right"
  consumer_0041 = "key_enter"
  consumer_0224 = "key_escape"
  consumer_00cd = "key_media_play_pause"
  consumer_00e2 = "key_volume_mute"
  consumer_00e9 = "key_volume_up"
  consumer_00ea = "key_volume_down"

[[remote_bridge.profiles]]
id = "freetube"
executables = ["FreeTube.exe"]
backend = "sendinput"
  [remote_bridge.profiles.bindings]
  up = "key_shift+tab"
  down = "key_tab"
  left = "key_left"
  right = "key_right"
  consumer_0041 = "key_enter"
  consumer_0224 = "key_escape"
  consumer_00cd = "key_space"
```

The built-in G20S mapping is:

| Remote input | Stremio gamepad action |
|---|---|
| D-pad | Xbox D-pad |
| OK / consumer `0x0041` | A / Select |
| Back / consumer `0x0224` | B / Back |
| Play/Pause / consumer `0x00CD` | X / player Play-Pause |
| Page Up / Page Down | Left / right shoulder |
| Mute / consumer `0x00E2` | Stock Windows mute input (not remapped) |
| Volume Up / Down | Stock Windows consumer-volume input (not remapped) |

Stremio 5's gamepad support must be enabled in Stremio's Interface settings. Its native gamepad path provides one-item focus navigation and player-specific actions, which is more reliable than translating the remote to hard-wired desktop keyboard shortcuts.

#### Input ownership and output backend status

Windows Raw Input can identify which keyboard/consumer-control interface produced an event, but a normal user-mode app cannot suppress only that device's legacy arrow keystrokes. For a permanent dedicated-remote setup, RunLights supports an `owned` path that rebinds only `USB\VID_4842&PID_0001&MI_02` to Microsoft's in-box WinUSB driver. The companion then reads that interface directly and emits the active profile. MI_03 mouse, MI_04 vendor HID, audio interfaces, normal keyboards, and physical controllers are not matched. RunLights does not install or sign this package automatically.

The narrow INF template and rollback notes are in `drivers/g20s-mi02`. It intentionally has no signed catalog in the repository. Windows will reject it until a catalog is created and signed; producing/trusting that certificate and installing the package are administrator-level changes that require explicit approval. The UI also refuses to save `owned` mode until exactly one matching RunLights WinUSB interface is present.

- `diagnostic` (default): safe input test and profile activation; no virtual device, injection, or suppression.
- `sendinput`: emits only configured named Windows navigation/media keys; available only with exclusive `owned` input.
- `vgamepad`: optional compatibility adapter only when the Python package and ViGEmBus are already installed. ViGEmBus is retired, so this is not installed or recommended automatically.
- HIDMaestro is a more recent virtual-controller option, but it still requires a one-time trusted system component and is not yet a RunLights backend. It must be reviewed and explicitly approved before installation.

Because the physical G20S also exposes a keyboard collection, virtual output alone does not prevent duplicate Stremio keyboard navigation. Use gamepad or key output only after the device-scoped MI_02 ownership step. Stremio still benefits from its native gamepad route for D-pad focus plus player-aware OK/Back/seek; per-app key profiles are most useful for applications such as FreeTube whose keyboard controls are suitable.

#### Safety, recovery, and removal

- Choose **Disable Remote / Stock Input** from the tray, click **Disable now / stock input** in **Debug → Remote**, or hold the remote's **Home + Back** buttons for three seconds, to create the immediate disable marker. Outputs are disconnected and held actions are released. In `owned` mode, “stock input” means the default RunLights profile; restoring the receiver's original hardware behavior requires the driver rollback documented in `drivers/g20s-mi02/README.md`.
- The output device is connected only while a configured target executable is the foreground app and is disconnected on focus loss, bridge shutdown, or RunLights shutdown.
- Set `remote_bridge.enabled = false` to stop the companion. To remove the feature completely, first roll MI_02 back to Microsoft `input.inf` if ownership was installed, then stop RunLights and delete the `[remote_bridge]` section plus `.runlights` (logs/status only). No driver or service is installed merely by enabling the bridge.
- If an optional third-party virtual-device stack was installed separately, remove it with that product's installer; deleting RunLights does not remove system drivers.

#### Manual verification checklist

Use **Debug → Remote** in `diagnostic` mode first:

1. Confirm only paths containing the configured VID/PID appear and remote presses populate the live log.
2. Type and navigate with a normal keyboard; no events from it should appear in the bridge log and its behavior must remain unchanged.
3. Test existing physical gamepads in Windows/Steam; diagnostic mode creates no controller and does not alter Steam Input.
4. Focus and unfocus `stremio-shell-ng.exe`; verify profile `stremio` changes between active and idle.
5. With any approved virtual backend, confirm the virtual controller appears only while Stremio is foreground and disappears immediately on focus loss or disable.
6. Verify menu D-pad focus, OK/select, player play-pause, Back, seek, volume, and held-button repeat. Full behavior verification depends on approved device suppression and cannot be claimed from diagnostic mode.

### Modular architecture

The `runlights.core` package supplies the shared snap-in points:

- `ModuleManager` owns start/stop order and isolates module failures.
- `EventBus` lets modules subscribe without importing one another.
- `ApplicationMonitorModule` publishes application started/stopped and foreground-change events once for lighting, remote mapping, launch actions, notifications, and future modules.
- `RemoteBridgeModule` supervises low-level input code in the companion rather than loading it into the main UI process.

New features should be modules with a small lifecycle (`start`, `stop`) and event subscriptions. Lighting should continue moving behind a `LightingModule` subscriber over time; this release moves shared application detection into the core while retaining the existing lighting behavior.

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
- Current ES-DE diagnostics use a single `game-select.pyw` directly in `D:\Emulators\EmulationStation\ES-DE\scripts\game-select`; it only logs the arguments ES-DE passes and does not send light commands.
- The diagnostic script writes to `D:\Emulators\EmulationStation\ES-DE\scripts\game-select\game-select-pyw.log`; check this file to confirm whether ES-DE passes the expected ROM, game, system name, and system full name.

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
- Clock overlay: right-click the clock to add a 15 minute, 30 minute, or 1 hour countdown timer. A small alarm icon appears beside the clock while a timer is running, hover shows the time left, and left-click drag moves the overlay for the current session. When the timer ends, the clock overlay flashes red until the timer is cancelled or replaced.
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
