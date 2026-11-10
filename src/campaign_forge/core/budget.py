"""Hard per-run limits on LLM calls, tokens, wall-clock time, revisions and repairs.

A :class:`Budget` is shared by every step of a run, including steps running in
parallel, so all accounting is lock-protected. Limits are checked *before* work
starts. A call is refused when its worst case (the completion-token cap) could
push the run over its token limit, so a run can never overspend by more than the
prompt tokens of the last call it admitted.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..domain.usage import TokenUsage
from ..exceptions import BudgetExceededError


class BudgetLimits(BaseModel):
    """Configurable ceilings for one run (defaults from docs/ARCHITECTURE.md T5)."""

    model_config = ConfigDict(frozen=True)

    max_llm_calls: int = Field(default=40, ge=1, le=1000)
    max_total_tokens: int = Field(default=60_000, ge=1, le=10_000_000)
    max_wall_seconds: float = Field(default=600.0, gt=0.0, le=86_400.0)
    max_revisions: int = Field(default=10, ge=0, le=100)
    max_repairs: int = Field(default=10, ge=0, le=100)
    max_tool_calls: int = Field(default=20, ge=0, le=200)


@dataclass(frozen=True)
class BudgetSnapshot:
    """A point-in-time view of consumption against limits."""

    limits: BudgetLimits
    llm_calls: int
    usage: TokenUsage
    elapsed_seconds: float
    revisions: int
    repairs: int
    tool_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


class Budget:
    """Thread-safe accounting against :class:`BudgetLimits`."""

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._calls = 0
        self._usage = TokenUsage()
        self._revisions = 0
        self._repairs = 0
        self._tool_calls = 0

    def reserve_call(self, max_completion_tokens: int = 0) -> None:
        """Admit one LLM call, or raise if it could breach a limit.

        Every admitted call counts, including one the provider then refuses
        (e.g. for quota): it still consumed a request against the provider's limits.

        Args:
            max_completion_tokens: The call's completion cap. It is counted as
                the worst case, so the run stays within ``max_total_tokens``.

        Raises:
            BudgetExceededError: with ``limit`` set to ``"wall_time"``,
                ``"llm_calls"`` or ``"tokens"``.
        """
        with self._lock:
            self._check_time()
            if self._calls >= self.limits.max_llm_calls:
                raise BudgetExceededError(
                    "llm_calls", f"Run reached its limit of {self.limits.max_llm_calls} LLM calls."
                )
            projected = self._usage.total_tokens + max_completion_tokens
            if projected > self.limits.max_total_tokens:
                raise BudgetExceededError(
                    "tokens",
                    f"Run would exceed its limit of {self.limits.max_total_tokens} tokens "
                    f"({self._usage.total_tokens} used, call may add {max_completion_tokens}).",
                )
            self._calls += 1

    def record_usage(self, usage: TokenUsage) -> None:
        """Add the measured usage of a completed call."""
        with self._lock:
            self._usage = self._usage + usage

    def charge_revision(self) -> None:
        """Count one critic-driven revision, or raise if revisions are exhausted."""
        with self._lock:
            self._check_time()
            if self._revisions >= self.limits.max_revisions:
                raise BudgetExceededError(
                    "revisions", f"Run reached its limit of {self.limits.max_revisions} revisions."
                )
            self._revisions += 1

    def charge_repair(self) -> None:
        """Count one output-repair re-ask, or raise if repairs are exhausted."""
        with self._lock:
            self._check_time()
            if self._repairs >= self.limits.max_repairs:
                raise BudgetExceededError(
                    "repairs", f"Run reached its limit of {self.limits.max_repairs} repairs."
                )
            self._repairs += 1

    def charge_tool_call(self) -> None:
        """Count one research-tool call, or raise if tool calls are exhausted."""
        with self._lock:
            self._check_time()
            if self._tool_calls >= self.limits.max_tool_calls:
                raise BudgetExceededError(
                    "tool_calls",
                    f"Run reached its limit of {self.limits.max_tool_calls} tool calls.",
                )
            self._tool_calls += 1

    def check_time(self) -> None:
        """Raise if the run has used up its wall-clock allowance."""
        with self._lock:
            self._check_time()

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(
                limits=self.limits,
                llm_calls=self._calls,
                usage=self._usage,
                elapsed_seconds=self._clock() - self._started,
                revisions=self._revisions,
                repairs=self._repairs,
                tool_calls=self._tool_calls,
            )

    def _check_time(self) -> None:
        elapsed = self._clock() - self._started
        if elapsed > self.limits.max_wall_seconds:
            raise BudgetExceededError(
                "wall_time",
                f"Run exceeded its wall-clock limit of {self.limits.max_wall_seconds:.0f}s.",
            )
