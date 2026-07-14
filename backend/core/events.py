from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Callable
import fnmatch
import uuid

from backend.core.logging import LoggerManager

EventHandler = Callable[["Event"], Any]

@dataclass(frozen=True)
class Event:
    event_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    workspace_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    priority: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    id: str = field(default_factory=lambda: f"event_{uuid.uuid4().hex}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class EventDeliveryResult:
    event_id: str
    event_type: str
    matched_handlers: int
    completed_handlers: int
    failed_handlers: int
    errors: list[str]

class EventBus:
    def __init__(self, history_size: int = 500) -> None:
        self._subscriptions: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._logger = LoggerManager.get_logger("events")

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        if not pattern.strip():
            raise ValueError("Event pattern cannot be empty.")
        if handler not in self._subscriptions[pattern]:
            self._subscriptions[pattern].append(handler)

    def unsubscribe(self, pattern: str, handler: EventHandler) -> None:
        handlers = self._subscriptions.get(pattern, [])
        if handler in handlers:
            handlers.remove(handler)
        if not handlers:
            self._subscriptions.pop(pattern, None)

    async def publish(self, event: Event) -> EventDeliveryResult:
        handlers = self._matching_handlers(event.event_type)
        errors: list[str] = []
        completed = failed = 0

        self._logger.info(
            "Event published id=%s type=%s source=%s handlers=%s",
            event.id, event.event_type, event.source, len(handlers)
        )

        for handler in handlers:
            try:
                result = handler(event)
                if isawaitable(result):
                    await result
                completed += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{self._handler_name(handler)}: {exc}")
                self._logger.exception(
                    "Event handler failed event_id=%s type=%s handler=%s",
                    event.id, event.event_type, self._handler_name(handler)
                )

        delivery = EventDeliveryResult(
            event_id=event.id,
            event_type=event.event_type,
            matched_handlers=len(handlers),
            completed_handlers=completed,
            failed_handlers=failed,
            errors=errors,
        )
        self._history.append({"event": event.to_dict(), "delivery": asdict(delivery)})
        return delivery

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [] if limit < 1 else list(self._history)[-limit:]

    def subscriptions(self) -> dict[str, list[str]]:
        return {
            pattern: [self._handler_name(h) for h in handlers]
            for pattern, handlers in self._subscriptions.items()
        }

    def _matching_handlers(self, event_type: str) -> list[EventHandler]:
        result, seen = [], set()
        for pattern, handlers in self._subscriptions.items():
            if fnmatch.fnmatchcase(event_type, pattern):
                for handler in handlers:
                    if id(handler) not in seen:
                        seen.add(id(handler))
                        result.append(handler)
        return result

    @staticmethod
    def _handler_name(handler: EventHandler) -> str:
        return getattr(handler, "__qualname__", repr(handler))

event_bus = EventBus()
