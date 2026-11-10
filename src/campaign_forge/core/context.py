"""The run context: the single gateway through which agents reach a model.

Every agent LLM call in 2.0 goes through :meth:`RunContext.complete`, which:

1. checks cancellation;
2. reserves the call against the run budget;
3. routes it to a provider, applying policy and any explicit fallback;
4. records the measured usage;
5. publishes events.

Agents therefore cannot bypass limits or make untracked calls.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import TypeVar

from pydantic import BaseModel

from ..config import AgentRoute
from ..domain.scope import Scope
from ..exceptions import AgentError, RunCancelledError
from ..providers.base import CompletionRequest, CompletionResponse, StructuredResult
from ..providers.router import ProviderRouter
from ..providers.structured import run_structured
from .agent import AgentSpec
from .budget import Budget, BudgetLimits
from .events import Event, EventBus, EventType

ModelT = TypeVar("ModelT", bound=BaseModel)


class CancellationToken:
    """Cooperative cancellation. A child token is cancelled when it or any parent is."""

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = threading.Event()
        self._parent = parent

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or (self._parent is not None and self._parent.cancelled)

    def child(self) -> CancellationToken:
        """A token that can be cancelled on its own (e.g. a timed-out step) or with this one."""
        return CancellationToken(parent=self)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelledError("The run was cancelled.")


class CallCounter:
    """Counts model calls made by one agent invocation against its ``max_calls`` limit."""

    def __init__(self) -> None:
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def charge(self, spec: AgentSpec) -> None:
        with self._lock:
            if self._calls >= spec.limits.max_calls:
                raise AgentError(
                    spec.id,
                    f"exceeded its limit of {spec.limits.max_calls} model calls per invocation.",
                )
            self._calls += 1


@dataclass
class RunContext:
    """Per-run services handed to every agent."""

    run_id: str
    router: ProviderRouter
    scope: Scope = field(default_factory=Scope.local)
    budget: Budget = field(default_factory=Budget)
    events: EventBus = field(default_factory=EventBus)
    cancel_token: CancellationToken = field(default_factory=CancellationToken)
    overrides: dict[str, AgentRoute] = field(default_factory=dict)
    step_id: str | None = None
    call_counter: CallCounter | None = None

    def for_step(self, step_id: str) -> RunContext:
        """A view of this context for one step: same budget and events, own cancel token."""
        return replace(self, step_id=step_id, cancel_token=self.cancel_token.child())

    def for_invocation(self) -> RunContext:
        """A view that enforces the invoked agent's ``max_calls`` limit."""
        return replace(self, call_counter=CallCounter())

    @classmethod
    def create(
        cls,
        router: ProviderRouter,
        *,
        scope: Scope | None = None,
        limits: BudgetLimits | None = None,
        events: EventBus | None = None,
        run_id: str | None = None,
    ) -> RunContext:
        return cls(
            run_id=run_id or f"run-{uuid.uuid4().hex[:12]}",
            router=router,
            scope=scope or Scope.local(),
            budget=Budget(limits),
            events=events or EventBus(),
        )

    def complete(
        self, spec: AgentSpec, request: CompletionRequest, *, step_id: str | None = None
    ) -> CompletionResponse:
        """Perform one budgeted, routed, observable LLM call on behalf of an agent."""
        step_id = step_id or self.step_id
        self.cancel_token.raise_if_cancelled()
        if self.call_counter is not None:
            self.call_counter.charge(spec)
        override = self.overrides.get(spec.id)
        # Precedence: request > run override > configured agent override > spec default.
        max_tokens = (
            request.max_tokens
            or self.router.configured_max_tokens(spec.id, override)
            or spec.limits.max_tokens
        )
        self.budget.reserve_call(max_tokens)
        metadata = {**request.metadata, "agent": spec.id, "run_id": self.run_id}
        response = self.router.complete(
            spec.id,
            request.model_copy(update={"max_tokens": max_tokens, "metadata": metadata}),
            model_class=spec.model_class,
            provider_preference=spec.provider,
            override=override,
        )
        self.budget.record_usage(response.usage)
        self.events.publish(
            Event(
                type=EventType.LLM_CALL_FINISHED,
                run_id=self.run_id,
                step_id=step_id,
                message=f"{spec.id}: {response.provider}/{response.model}",
                data={
                    "agent": spec.id,
                    "provider": response.provider,
                    "model": response.model,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "usage_complete": response.usage.complete,
                    "latency_s": round(response.latency_s, 3),
                    "attempts": response.attempts,
                },
            )
        )
        if response.fallback_from:
            self.events.publish(
                Event(
                    type=EventType.PROVIDER_FALLBACK,
                    run_id=self.run_id,
                    step_id=step_id,
                    message=(
                        f"{spec.id}: {response.fallback_from} unavailable; "
                        f"used fallback {response.provider}/{response.model}"
                    ),
                    data={
                        "agent": spec.id,
                        "from": response.fallback_from,
                        "to": response.provider,
                    },
                )
            )
        return response

    def complete_structured(
        self,
        spec: AgentSpec,
        request: CompletionRequest,
        schema: type[ModelT],
        *,
        step_id: str | None = None,
    ) -> StructuredResult[ModelT]:
        """Structured output with repairs, each re-ask charged to the run budget."""
        step_id = step_id or self.step_id
        return run_structured(
            lambda r: self.complete(spec, r, step_id=step_id),
            request.model_copy(update={"metadata": {**request.metadata, "agent": spec.id}}),
            schema,
            max_repairs=spec.limits.max_repairs,
            on_repair=self.budget.charge_repair,
        )
