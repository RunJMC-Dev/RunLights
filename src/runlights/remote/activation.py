from __future__ import annotations

from collections.abc import Callable

from .models import AppProfile


class ProfileActivator:
    def __init__(self, profiles: list[AppProfile], on_change: Callable[[AppProfile | None], None]):
        self._profiles = profiles
        self._on_change = on_change
        self.active: AppProfile | None = None

    def update(self, foreground_executable: str | None) -> AppProfile | None:
        selected = next(
            (
                profile
                for profile in self._profiles
                if not profile.default and profile.matches_executable(foreground_executable)
            ),
            None,
        )
        if selected is None:
            selected = next((profile for profile in self._profiles if profile.default), None)
        if selected != self.active:
            self.active = selected
            self._on_change(selected)
        return selected

    def deactivate(self) -> None:
        if self.active is not None:
            self.active = None
            self._on_change(None)
