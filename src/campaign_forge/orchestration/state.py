"""Run and step state, with the run lifecycle as an explicit, checked state machine.

The allowed transitions follow ``docs/ARCHITECTURE.md`` (T6), plus two recovery
paths:

* ``FAILED -> RUNNING`` (retry failed steps);
* ``RUNNING -> RUNNING`` (resume a run that was interrupted by a crash).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import AgentRoute
from ..core.budget import BudgetLimits
from ..core.events import Event
from ..domain.artifacts import Artifact
from ..domain.scope import Scope
from ..domain.usage import TokenUsage
from ..exceptions import RunStateError
from .definition import WorkflowDefinition, parse_ref

RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
MAX_EVENTS = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELLED = "cancelled"


TERMINAL_STEP_STATES = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED}),
    RunStatus.PLANNING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.PAUSED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_FOR_APPROVAL: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.PAUSED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.FAILED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class SkipReason(str, Enum):
    CONDITION = "condition"
    DISABLED = "disabled"
    DEPENDENCY = "dependency"


class StepState(BaseModel):
    """The persisted state of one step."""

    step_id: str
    agent: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    revisions: int = 0
    artifact_id: str | None = None
    review_artifact_id: str | None = None
    review_score: float | None = None
    input_fingerprint: str | None = None
    skip_reason: SkipReason | None = None
    detail: str | None = None
    error: str | None = None
    error_type: str | None = None
    notes: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STEP_STATES

    def reset(self) -> None:
        """Return the step to PENDING (keeping its attempt count for diagnostics)."""
        self.status = StepStatus.PENDING
        self.artifact_id = None
        self.review_artifact_id = None
        self.review_score = None
        self.input_fingerprint = None
        self.skip_reason = None
        self.detail = None
        self.error = None
        self.error_type = None
        self.notes = ()
        self.revisions = 0
        self.started_at = None
        self.finished_at = None


class RunRecord(BaseModel):
    """Everything needed to inspect, resume, approve or cancel a run."""

    model_config = ConfigDict(validate_assignment=False)

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    scope: Scope
    definition: WorkflowDefinition
    status: RunStatus = RunStatus.PENDING
    status_reason: str | None = None
    resume_after: float | None = None
    limits: BudgetLimits
    overrides: dict[str, AgentRoute] = Field(default_factory=dict)
    inputs: dict[str, Artifact]
    steps: dict[str, StepState]
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    usage: TokenUsage = TokenUsage()
    llm_calls: int = 0
    events: list[Event] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        scope: Scope,
        definition: WorkflowDefinition,
        inputs: dict[str, Artifact],
        limits: BudgetLimits,
        overrides: dict[str, AgentRoute] | None = None,
    ) -> RunRecord:
        return cls(
            run_id=run_id,
            scope=scope,
            definition=definition,
            limits=limits,
            overrides=overrides or {},
            inputs=inputs,
            steps={s.id: StepState(step_id=s.id, agent=s.agent) for s in definition.steps},
        )

    def set_status(self, status: RunStatus, reason: str | None = None) -> None:
        """Move to ``status`` if the lifecycle allows it.

        Raises:
            RunStateError: on an illegal transition.
        """
        if status not in ALLOWED_TRANSITIONS[self.status]:
            raise RunStateError(
                f"Run {self.run_id} cannot move from {self.status.value} to {status.value}."
            )
        self.status = status
        self.status_reason = reason
        if status is not RunStatus.PAUSED:
            self.resume_after = None
        self.touch()

    def touch(self) -> None:
        self.updated_at = _now()

    def add_event(self, event: Event) -> None:
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]

    def resolve(self, ref: str) -> Artifact | None:
        """The artifact a reference points to, if it exists yet."""
        kind, name = parse_ref(ref)
        if kind == "input":
            return self.inputs.get(name)
        state = self.steps.get(name)
        if state is None or state.artifact_id is None:
            return None
        return self.artifacts.get(state.artifact_id)

    def outputs(self) -> dict[str, Artifact]:
        """The workflow's declared outputs that exist."""
        result: dict[str, Artifact] = {}
        for name, ref in self.definition.outputs.items():
            artifact = self.resolve(ref)
            if artifact is not None and self._ref_succeeded(ref):
                result[name] = artifact
        return result

    def _ref_succeeded(self, ref: str) -> bool:
        kind, name = parse_ref(ref)
        return kind == "input" or self.steps[name].status is StepStatus.SUCCEEDED

    def summary(self) -> dict[str, Any]:
        """A compact, JSON-friendly overview (CLI and API listings)."""
        return {
            "run_id": self.run_id,
            "workflow": f"{self.definition.id}@{self.definition.version}",
            "status": self.status.value,
            "status_reason": self.status_reason,
            "resume_after": self.resume_after,
            "steps": {sid: s.status.value for sid, s in self.steps.items()},
            "llm_calls": self.llm_calls,
            "total_tokens": self.usage.total_tokens,
            "usage_complete": self.usage.complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
