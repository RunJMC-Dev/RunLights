from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, DefaultDict, Generic, TypeVar


EventT = TypeVar("EventT")
EventHandler = Callable[[Any], None]


@dataclass(frozen=True)
class Subscription(Generic[EventT]):
    event_type: type[EventT]
    handler: Callable[[EventT], None]


class EventBus:
    """Small synchronous event bus used to decouple RunLights modules.

    Handlers run in the publisher's thread. One failing module is isolated from
    the others and reported through ``on_error``.
    """

    def __init__(self, on_error: Callable[[str], None] | None = None):
        self._handlers: DefaultDict[type[Any], list[EventHandler]] = defaultdict(list)
        self._lock = RLock()
        self._on_error = on_error or (lambda _message: None)

    def subscribe(
        self, event_type: type[EventT], handler: Callable[[EventT], None]
    ) -> Subscription[EventT]:
        with self._lock:
            self._handlers[event_type].append(handler)
        return Subscription(event_type, handler)

    def unsubscribe(self, subscription: Subscription[Any]) -> None:
        with self._lock:
            handlers = self._handlers.get(subscription.event_type, [])
            if subscription.handler in handlers:
                handlers.remove(subscription.handler)

    def publish(self, event: object) -> None:
        with self._lock:
            handlers = list(self._handlers.get(type(event), ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                name = getattr(handler, "__qualname__", repr(handler))
                self._on_error(f"Module event handler {name} failed: {exc}")
