"""Live mode for the public demo (Session 9, ADR-037): caps that protect a free quota.

The public app answers with a simulated responder by default. Live mode calls
Groq instead, with the owner's key (offered only when the owner opts in) or a
visitor's own key. Every live run is bounded:

* **Per run:** at most ``CF_LIVE_RUN_CALLS`` model calls, ``CF_LIVE_RUN_TOKENS``
  tokens and ``CF_LIVE_RUN_SECONDS`` of wall time. The plan's budget is tightened
  to these values, never loosened.
* **At once:** at most ``CF_LIVE_CONCURRENT_RUNS`` live runs execute in the
  process, whichever key they use (one shared :class:`~.runs.RunSlots`).
* **Owner key only:** ``CF_LIVE_SESSION_RUNS`` runs per browser session and
  ``CF_LIVE_DAILY_RUNS`` per UTC day. A visitor's key spends the visitor's quota.

The counters live in process memory: they reset when the app restarts, and a
page reload starts a new browser session. The hard bound on the owner's spending
is the free tier itself. No card is on file, so the worst case is a used-up daily
quota, never a bill.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..exceptions import LiveQuotaError
from ..orchestration.definition import WorkflowDefinition
from .plans import tighten
from .runs import RunSlots


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class LiveLimits(BaseModel):
    """The live-mode caps (from ``CF_LIVE_*``)."""

    model_config = ConfigDict(frozen=True)

    session_runs: int
    daily_runs: int
    concurrent_runs: int
    run_calls: int
    run_tokens: int
    run_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> LiveLimits:
        return cls(
            session_runs=settings.live_session_runs,
            daily_runs=settings.live_daily_runs,
            concurrent_runs=settings.live_concurrent_runs,
            run_calls=settings.live_run_calls,
            run_tokens=settings.live_run_tokens,
            run_seconds=settings.live_run_seconds,
        )

    def cap(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        """``definition`` with its budget tightened to the per-run caps."""
        limits = tighten(
            definition.limits,
            max_llm_calls=self.run_calls,
            max_total_tokens=self.run_tokens,
            max_wall_seconds=self.run_seconds,
        )
        return definition.model_copy(update={"limits": limits})

    def describe(self) -> str:
        minutes = self.run_seconds / 60
        return f"{self.run_calls} model calls, {self.run_tokens:,} tokens and {minutes:g} minutes"


class LiveQuota:
    """Process-wide live-run accounting: the shared run slots and the owner key's daily cap.

    One instance serves every browser session of an app process.
    """

    def __init__(self, limits: LiveLimits, *, today: Callable[[], date] = _utc_today) -> None:
        self.limits = limits
        self.slots = RunSlots(limits.concurrent_runs)
        self._today = today
        self._day = today()
        self._owner_runs = 0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        """Start a new count when the UTC day changes (call with the lock held)."""
        today = self._today()
        if today != self._day:
            self._day = today
            self._owner_runs = 0

    def owner_runs_today(self) -> int:
        with self._lock:
            self._roll()
            return self._owner_runs

    def owner_runs_left_today(self) -> int:
        return max(self.limits.daily_runs - self.owner_runs_today(), 0)

    def reserve_owner_run(self) -> None:
        """Count one owner-key run against today's cap.

        Raises:
            LiveQuotaError: if today's owner-key runs are used up.
        """
        with self._lock:
            self._roll()
            if self._owner_runs >= self.limits.daily_runs:
                raise LiveQuotaError(
                    f"The demo key's {self.limits.daily_runs} live runs for today are used up. "
                    "Add your own free Groq key to keep going, or try again after 00:00 UTC."
                )
            self._owner_runs += 1

    def refund_owner_run(self) -> None:
        """Return a reservation whose run never started (it failed validation, say)."""
        with self._lock:
            self._roll()
            self._owner_runs = max(self._owner_runs - 1, 0)
