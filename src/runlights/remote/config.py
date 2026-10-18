from __future__ import annotations

from .models import AppProfile, DeviceDefinition, RemoteBridgeConfig


DEFAULT_BINDINGS = {
    "up": "dpad_up",
    "down": "dpad_down",
    "left": "dpad_left",
    "right": "dpad_right",
    "page_up": "left_shoulder",
    "page_down": "right_shoulder",
    "consumer_0041": "button_a",
    "consumer_0224": "button_b",
    "consumer_00cd": "button_x",
    "consumer_0223": "guide",
    "consumer_00e9": "passthrough",
    "consumer_00ea": "passthrough",
    "consumer_00cf": "ignore",
}


def _as_int(value: object, default: int) -> int:
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def load_remote_bridge_config(raw: dict) -> RemoteBridgeConfig:
    section = raw.get("remote_bridge") or {}
    default_device = DeviceDefinition("g20s", 0x4842, 0x0001)
    devices: list[DeviceDefinition] = []
    for item in section.get("devices", []):
        if not isinstance(item, dict):
            continue
        device_id = str(item.get("id", "")).strip()
        if not device_id:
            continue
        devices.append(
            DeviceDefinition(
                device_id,
                _as_int(item.get("vid"), default_device.vid),
                _as_int(item.get("pid"), default_device.pid),
                str(item.get("path_contains", "")).strip(),
            )
        )
    if not devices:
        devices = [default_device]

    profiles: list[AppProfile] = []
    for item in section.get("profiles", []):
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id", "")).strip()
        executables = tuple(
            str(value).strip()
            for value in item.get("executables", [])
            if str(value).strip()
        )
        if not profile_id or not executables:
            continue
        bindings = dict(DEFAULT_BINDINGS)
        bindings.update(
            {
                str(key).casefold(): str(value).casefold()
                for key, value in (item.get("bindings") or {}).items()
            }
        )
        profiles.append(
            AppProfile(
                profile_id,
                executables,
                bindings,
                str(item.get("backend", section.get("backend", "diagnostic"))).casefold(),
            )
        )
    if not profiles:
        profiles = [
            AppProfile(
                "stremio",
                ("stremio-shell-ng.exe",),
                dict(DEFAULT_BINDINGS),
                str(section.get("backend", "diagnostic")).casefold(),
            )
        ]

    return RemoteBridgeConfig(
        enabled=bool(section.get("enabled", False)),
        device_id=str(section.get("device_id", "g20s")),
        backend=str(section.get("backend", "diagnostic")).casefold(),
        repeat_delay_ms=max(0, _as_int(section.get("repeat_delay_ms"), 425)),
        repeat_interval_ms=max(10, _as_int(section.get("repeat_interval_ms"), 90)),
        escape_hold_seconds=max(1.0, _as_float(section.get("escape_hold_seconds"), 3.0)),
        devices=devices,
        profiles=profiles,
    )
