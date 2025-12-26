from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import tomllib


class ConfigError(Exception):
    """Raised when config cannot be loaded or is invalid."""


@dataclass
class Config:
    raw: Dict[str, Any]
    path: Path

    def find_esde_binding(self, console_name: str) -> Optional[Dict[str, Any]]:
        """Return the binding dict for a given console name, if present."""
        apps = self.raw.get("application", [])
        for app in apps:
            if app.get("id") != "esde":
                continue
            modes = app.get("modes", [])
            for mode in modes:
                if mode.get("id") != "game-select":
                    continue
                bindings = mode.get("bindings", {})
                return bindings.get(console_name)
        return None


def load_config(path: Path | str = "config.toml") -> Config:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # broad catch to surface file issues
        raise ConfigError(f"Failed to parse config: {exc}") from exc
    return Config(raw=data, path=config_path)

