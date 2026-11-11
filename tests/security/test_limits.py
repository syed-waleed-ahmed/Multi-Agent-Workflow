"""The sliding-window limiter behind the API's per-key request and run-start limits."""

from __future__ import annotations

import pytest

from campaign_forge.security import limits
from campaign_forge.security.limits import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_window_allows_up_to_the_limit_then_reports_the_wait() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, 60.0, clock=clock)
    assert limiter.check("a") is None and limiter.check("a") is None
    assert limiter.check("a") == pytest.approx(60.0)
    assert limiter.check("b") is None  # keys are independent
    clock.now += 30
    assert limiter.check("a") == pytest.approx(30.0)
    clock.now += 30.001
    assert limiter.check("a") is None  # the first event left the window


def test_idle_keys_are_pruned_and_bad_settings_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "_PRUNE_AT", 3)
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 10.0, clock=clock)
    for key in ("a", "b", "c"):
        limiter.check(key)
    clock.now += 11
    limiter.check("d")  # four keys: the three idle ones are dropped
    assert set(limiter._events) == {"d"}  # noqa: SLF001
    for limit, window in ((0, 60.0), (1, 0.0)):
        with pytest.raises(ValueError):
            SlidingWindowLimiter(limit, window)
