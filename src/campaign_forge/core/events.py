"""Typed run events and a fault-isolated event bus.

Events feed:

* the CLI progress display;
* the Streamlit run inspector;
* the API status endpoint;
* the run history.

A subscriber that raises is logged and skipped. It can never break the run that
published the event (the lesson of defect D5).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..logging_config import get_logger


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    LLM_CALL_FINISHED = "llm_call_finished"
    TOOL_CALL_FINISHED = "tool_call_finished"
    PROVIDER_FALLBACK = "provider_fallback"
    APPROVAL_REQUESTED = "approval_requested"
    WARNING = "warning"


class Event(BaseModel):
    """One observable thing that happened during a run."""

    model_config = ConfigDict(frozen=True)

    type: EventType
    run_id: str
    step_id: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


Subscriber = Callable[[Event], None]


class EventBus:
    """Synchronous publish/subscribe with optional bounded history."""

    def __init__(self, *, history_size: int = 0) -> None:
        self._subscribers: list[Subscriber] = []
        self._history: deque[Event] = deque(maxlen=history_size or None)
        self._keep_history = history_size > 0
        self._lock = threading.Lock()
        self._log = get_logger("events")

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Add a subscriber; returns a function that removes it."""
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
            if self._keep_history:
                self._history.append(event)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                self._log.exception("Event subscriber failed on %s; continuing.", event.type.value)

    @property
    def history(self) -> list[Event]:
        with self._lock:
            return list(self._history)
