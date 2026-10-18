from __future__ import annotations

from typing import Callable, Protocol


class RunLightsModule(Protocol):
    """Lifecycle contract for an isolated RunLights snap-in module."""

    @property
    def name(self) -> str: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


class ModuleManager:
    def __init__(self, log: Callable[[str], None]):
        self._log = log
        self._modules: list[RunLightsModule] = []
        self._started: list[RunLightsModule] = []

    def add(self, module: RunLightsModule) -> None:
        self._modules.append(module)

    def start_all(self) -> None:
        for module in self._modules:
            try:
                module.start()
                self._started.append(module)
                self._log(f"Module started: {module.name}")
            except Exception as exc:
                self._log(f"Module failed to start: {module.name}: {exc}")

    def stop_all(self) -> None:
        for module in reversed(self._started):
            try:
                module.stop()
                self._log(f"Module stopped: {module.name}")
            except Exception as exc:
                self._log(f"Module failed to stop cleanly: {module.name}: {exc}")
        self._started.clear()
