from __future__ import annotations

from dataclasses import dataclass

from .models import ActionEvent


@dataclass
class _HeldAction:
    pressed_at: float
    next_repeat_at: float


class KeyStateTracker:
    """Normalises duplicate hardware events and produces controlled repeats."""

    def __init__(self, repeat_delay_ms: int = 425, repeat_interval_ms: int = 90):
        self.repeat_delay = repeat_delay_ms / 1000.0
        self.repeat_interval = repeat_interval_ms / 1000.0
        self._held: dict[str, _HeldAction] = {}

    @property
    def held_actions(self) -> frozenset[str]:
        return frozenset(self._held)

    def update(self, action: str, pressed: bool, now: float) -> list[ActionEvent]:
        if pressed:
            if action in self._held:
                return []
            self._held[action] = _HeldAction(now, now + self.repeat_delay)
            return [ActionEvent(action, True, now)]
        if action not in self._held:
            return []
        del self._held[action]
        return [ActionEvent(action, False, now)]

    def repeats(self, now: float) -> list[ActionEvent]:
        events: list[ActionEvent] = []
        for action, held in self._held.items():
            while now >= held.next_repeat_at:
                events.append(ActionEvent(action, True, held.next_repeat_at, repeat=True))
                held.next_repeat_at += self.repeat_interval
        return events

    def release_all(self, now: float) -> list[ActionEvent]:
        events = [ActionEvent(action, False, now) for action in tuple(self._held)]
        self._held.clear()
        return events
