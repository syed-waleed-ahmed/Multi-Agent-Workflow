"""Request limits: a thread-safe sliding-window counter per key.

Held in process memory, so limits reset when the process restarts and apply per
process (single node, ADR-009). That is the documented scope for the API and the
public demo; a shared store would be needed to limit across replicas.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

#: Keys with no recent requests are dropped once the table reaches this size.
_PRUNE_AT = 10_000


class SlidingWindowLimiter:
    """At most ``limit`` events per ``window_s`` seconds for each key."""

    def __init__(
        self, limit: int, window_s: float = 60.0, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit must be >= 1 and window_s > 0")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record an event for ``key``. Returns ``None`` if allowed, else seconds to wait."""
        now = self._clock()
        cutoff = now - self.window_s
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return max(events[0] + self.window_s - now, 0.001)
            events.append(now)
            if len(self._events) > _PRUNE_AT:
                self._prune(cutoff)
            return None

    def _prune(self, cutoff: float) -> None:
        for stale in [k for k, q in self._events.items() if not q or q[-1] <= cutoff]:
            del self._events[stale]
