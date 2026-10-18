from __future__ import annotations

from dataclasses import dataclass, field


def _hex_token(value: int) -> str:
    return f"{value:04X}"


@dataclass(frozen=True)
class DeviceDefinition:
    id: str
    vid: int
    pid: int
    path_contains: str = ""
    interface_guid: str = "{7E3D4E85-3AF6-4D82-A5D4-9B44C848E718}"

    def matches(self, device_path: str) -> bool:
        path = device_path.upper()
        if f"VID_{_hex_token(self.vid)}" not in path:
            return False
        if f"PID_{_hex_token(self.pid)}" not in path:
            return False
        return not self.path_contains or self.path_contains.upper() in path


@dataclass(frozen=True)
class AppProfile:
    id: str
    executables: tuple[str, ...]
    bindings: dict[str, str]
    backend: str = "diagnostic"
    default: bool = False

    def matches_executable(self, executable: str | None) -> bool:
        if not executable:
            return False
        name = executable.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].casefold()
        return name in {item.casefold() for item in self.executables}


@dataclass(frozen=True)
class InputEvent:
    device_path: str
    control: str
    pressed: bool
    timestamp: float
    hardware_repeat: bool = False


@dataclass(frozen=True)
class ActionEvent:
    action: str
    pressed: bool
    timestamp: float
    repeat: bool = False


@dataclass
class RemoteBridgeConfig:
    enabled: bool = False
    device_id: str = "g20s"
    backend: str = "diagnostic"
    input_mode: str = "observe"
    repeat_delay_ms: int = 425
    repeat_interval_ms: int = 90
    escape_hold_seconds: float = 3.0
    devices: list[DeviceDefinition] = field(default_factory=list)
    profiles: list[AppProfile] = field(default_factory=list)

    def selected_device(self) -> DeviceDefinition | None:
        return next((device for device in self.devices if device.id == self.device_id), None)
