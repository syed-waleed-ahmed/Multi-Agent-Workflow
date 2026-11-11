"""The workflow engine: deterministic, bounded, resumable multi-agent execution.

Rules (docs/ARCHITECTURE.md, T5):

* **Ready-set scheduling.** A step runs when every step it depends on has
  finished. Independent ready steps run in parallel, up to ``max_parallel``.
* **Failure isolation.** A failed step skips only the steps that need its output.
  Independent branches continue, and completed artifacts are kept as partial
  results.
* **Bounded loops.** Retries (``retries``), critic revisions (``max_revisions``),
  the run :class:`~campaign_forge.core.budget.Budget`, each agent's
  ``max_calls``, and a per-step timeout all have hard limits.
* **Pause, don't lose.**
  - A quota or rate-limit error that was not retried pauses the run, with a
    ``resume_after`` hint.
  - Exhausting the budget also pauses it.
  - The affected step goes back to pending, and every finished step stays done.
* **Approval gates.** A step marked ``approval`` waits for a human. Its
  dependants are blocked, while other branches carry on.
* **Idempotent resume.** Each step's result is keyed by a fingerprint of its
  definition, the agent version and the ids of its input artifacts. Resuming
  reuses matching results and re-runs only what changed, failed or was never run.

The main loop is the only code that mutates the run record. Worker threads run
agents and return results. A step that exceeds its timeout is abandoned: its
cancel token is set, and its thread ends at its next model call.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import AgentRoute
from ..core.agent import Agent, AgentOutput, AgentSpec
from ..core.budget import Budget, BudgetLimits
from ..core.context import CancellationToken, RunContext
from ..core.events import Event, EventBus, EventType
from ..core.registry import AgentRegistry
from ..domain.artifacts import Artifact, ProducedBy
from ..domain.review import REVIEW_FEEDBACK_INPUT, REVIEW_SUBJECT_INPUT, ReviewResult
from ..domain.scope import Scope
from ..exceptions import (
    AgentError,
    BudgetExceededError,
    QuotaExhaustedError,
    RateLimitedError,
    RunCancelledError,
    RunStateError,
    StepTimeoutError,
    WorkflowValidationError,
)
from ..logging_config import get_logger
from ..providers.router import ProviderRouter
from ..security.redaction import redact
from ..tools.base import ToolBox
from .definition import StepDefinition, WorkflowDefinition, parse_ref, validate_definition
from .state import RunRecord, RunStatus, SkipReason, StepState, StepStatus
from .store import InMemoryRunStore, RunStore

ModelT = TypeVar("ModelT", bound=BaseModel)

_PAUSE_ERRORS = (QuotaExhaustedError, RateLimitedError, BudgetExceededError)
_POLL_SECONDS = 0.25


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


@dataclass
class RunResult:
    """The outcome of an engine call. ``errors`` holds the original exceptions (not persisted)."""

    record: RunRecord
    errors: dict[str, BaseException] = field(default_factory=dict)

    @property
    def status(self) -> RunStatus:
        return self.record.status

    @property
    def ok(self) -> bool:
        return self.record.status is RunStatus.COMPLETED

    def outputs(self) -> dict[str, Artifact]:
        return self.record.outputs()

    def output(self, name: str, model_cls: type[ModelT]) -> ModelT:
        """A declared output, validated into its typed model."""
        try:
            artifact = self.outputs()[name]
        except KeyError:
            raise KeyError(
                f"Output {name!r} is not available (run is {self.status.value})."
            ) from None
        return artifact.as_model(model_cls)

    def first_error(self) -> BaseException | None:
        """The first recorded failure, following the workflow's step order."""
        for step_id in self.record.definition.topological_order():
            if step_id in self.errors:
                return self.errors[step_id]
        return next(iter(self.errors.values()), None)


@dataclass(frozen=True)
class _StepOutcome:
    artifact: Artifact
    review: Artifact | None
    score: float | None
    revisions: int
    notes: tuple[str, ...]


@dataclass
class _Attempt:
    future: Future[_StepOutcome]
    token: CancellationToken
    deadline: float


def fingerprint(step: StepDefinition, spec: AgentSpec, inputs: Mapping[str, Artifact]) -> str:
    """A stable key for "this step, this agent version, these exact inputs"."""
    payload = {
        "step": step.model_dump(mode="json"),
        "agent": [spec.id, spec.version, spec.prompt_version],
        "inputs": {name: inputs[name].id for name in sorted(inputs)},
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class WorkflowEngine:
    """Runs, resumes, approves and cancels workflow runs."""

    def __init__(
        self,
        registry: AgentRegistry,
        router: ProviderRouter,
        *,
        store: RunStore | None = None,
        events: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
        tools: ToolBox | None = None,
    ) -> None:
        self._registry = registry
        self._tools = tools
        self._router = router
        self._store: RunStore = store or InMemoryRunStore()
        self._events = events or EventBus()
        self._clock = clock
        self._active: dict[tuple[Scope, str], CancellationToken] = {}
        self._lock = threading.Lock()
        self._log = get_logger("engine")

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def store(self) -> RunStore:
        return self._store

    @property
    def router(self) -> ProviderRouter:
        return self._router

    @property
    def events(self) -> EventBus:
        """Engine-wide event stream (every run publishes here)."""
        return self._events

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def validate(self, definition: WorkflowDefinition) -> None:
        validate_definition(definition, self._registry)

    def start(
        self,
        definition: WorkflowDefinition,
        inputs: Mapping[str, BaseModel | Artifact],
        *,
        scope: Scope | None = None,
        run_id: str | None = None,
        limits: BudgetLimits | None = None,
        overrides: Mapping[str, AgentRoute] | None = None,
    ) -> RunResult:
        """Validate and run a workflow to completion, a pause, or an approval gate.

        Raises:
            WorkflowValidationError: if the definition or inputs are invalid. No run
                is created in that case.
        """
        self.validate(definition)
        record = RunRecord.new(
            run_id=run_id or new_run_id(),
            scope=scope or Scope.local(),
            definition=definition,
            inputs=self._input_artifacts(definition, inputs),
            limits=limits or definition.limits,
            overrides=dict(overrides or {}),
        )
        record.set_status(RunStatus.PLANNING, "definition validated")
        record.set_status(RunStatus.RUNNING)
        self._store.save(record)
        return self._execute(record, started=True)

    def resume(
        self,
        run_id: str,
        *,
        scope: Scope | None = None,
        rerun: Iterable[str] = (),
        limits: BudgetLimits | None = None,
    ) -> RunResult:
        """Continue a paused, failed or interrupted run.

        Args:
            rerun: Step ids to run again even though they succeeded. Their
                dependants are re-run as well.
            limits: A new budget for this execution session.
        """
        scope = scope or Scope.local()
        record = self._store.load(run_id, scope)
        self._ensure_inactive(record)
        if record.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            raise RunStateError(f"Run {run_id} is {record.status.value}; start a new run instead.")
        if record.status is RunStatus.WAITING_FOR_APPROVAL:
            raise RunStateError(f"Run {run_id} is waiting for approval; approve or reject it.")
        self.validate(record.definition)
        forced = set(rerun)
        unknown = forced - set(record.steps)
        if unknown:
            raise RunStateError(f"Unknown step(s) to re-run: {', '.join(sorted(unknown))}.")
        if limits is not None:
            record.limits = limits
        self._prepare_resume(record, forced)
        record.set_status(RunStatus.RUNNING, "resumed")
        self._store.save(record)
        return self._execute(record)

    def run_until_settled(
        self,
        result: RunResult,
        *,
        max_resumes: int,
        max_wait_s: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> RunResult:
        """Resume a run paused by a rate limit, waiting out the provider's hint.

        For unattended use (CLI ``--auto-resume``, the eval harness). It stops after
        ``max_resumes``, on any other pause reason, or when the hint is longer than
        ``max_wait_s``. Budget pauses and exhausted quotas are never auto-resumed: a
        daily quota is a rolling window, so its short hint frees room for about one
        call, and resuming would only crawl (D18, measured live).
        """
        for _ in range(max_resumes):
            record = result.record
            if record.status is not RunStatus.PAUSED or record.status_reason != "rate limited":
                break
            wait_s = record.resume_after if record.resume_after is not None else 60.0
            if wait_s > max_wait_s:
                break
            self._log.info(
                "Run %s paused (%s); resuming in %.0fs.",
                record.run_id,
                record.status_reason,
                wait_s,
            )
            sleep(wait_s)
            resumed = self.resume(record.run_id, scope=record.scope)
            result = RunResult(resumed.record, {**result.errors, **resumed.errors})
        return result

    def approve(
        self,
        run_id: str,
        step_id: str,
        *,
        scope: Scope | None = None,
        approved: bool = True,
        edited_data: Mapping[str, Any] | None = None,
        comment: str | None = None,
    ) -> RunResult:
        """Approve (optionally with edits) or reject a step that waits for a human.

        Approval continues the run. Rejection cancels it.
        """
        scope = scope or Scope.local()
        record = self._store.load(run_id, scope)
        self._ensure_inactive(record)
        state = record.steps.get(step_id)
        if state is None or state.status is not StepStatus.WAITING_FOR_APPROVAL:
            raise RunStateError(f"Step {step_id!r} of run {run_id} is not waiting for approval.")
        if not approved:
            state.status = StepStatus.FAILED
            state.error = comment or "Rejected by reviewer."
            state.error_type = "Rejected"
            state.finished_at = _now()
            self._cancel_record(record, f"step {step_id} rejected")
            return RunResult(record)
        if edited_data is not None and state.artifact_id is not None:
            original = record.artifacts[state.artifact_id]
            edited = Artifact(
                kind=original.kind,
                data=dict(edited_data),
                produced_by=ProducedBy(agent_id="human", agent_version="edit"),
            )
            record.artifacts[edited.id] = edited
            state.artifact_id = edited.id
            state.notes = (*state.notes, "edited by reviewer")
        state.status = StepStatus.SUCCEEDED
        state.detail = comment or "approved"
        self._publish_direct(record, EventType.STEP_FINISHED, f"{step_id} approved", step_id)
        if record.status is RunStatus.WAITING_FOR_APPROVAL:
            record.set_status(RunStatus.RUNNING, f"step {step_id} approved")
            self._store.save(record)
            return self._execute(record)
        self._store.save(record)
        return RunResult(record)

    def cancel(self, run_id: str, *, scope: Scope | None = None) -> RunRecord:
        """Cancel a run. A run executing in this process stops at its next safe point."""
        scope = scope or Scope.local()
        with self._lock:
            token = self._active.get((scope, run_id))
        if token is not None:
            token.cancel()
            return self._store.load(run_id, scope)
        record = self._store.load(run_id, scope)
        if record.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            raise RunStateError(f"Run {run_id} is already {record.status.value}.")
        self._cancel_record(record, "cancelled by user")
        return record

    def get(self, run_id: str, *, scope: Scope | None = None) -> RunRecord:
        return self._store.load(run_id, scope or Scope.local())

    def list_runs(self, *, scope: Scope | None = None) -> list[RunRecord]:
        return self._store.list_runs(scope or Scope.local())

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def _execute(self, record: RunRecord, *, started: bool = False) -> RunResult:
        definition = record.definition
        key = (record.scope, record.run_id)
        budget = Budget(record.limits, clock=self._clock)
        bus = EventBus()
        record_lock = threading.Lock()

        def on_event(event: Event) -> None:
            with record_lock:
                record.add_event(event)
            self._events.publish(event)

        bus.subscribe(on_event)
        ctx = RunContext(
            run_id=record.run_id,
            router=self._router,
            scope=record.scope,
            budget=budget,
            events=bus,
            overrides=dict(record.overrides),
            tools=self._tools,
        )
        base_usage, base_calls = record.usage, record.llm_calls

        def save() -> None:
            snapshot = budget.snapshot()
            with record_lock:
                record.usage = base_usage + snapshot.usage
                record.llm_calls = base_calls + snapshot.llm_calls
                record.touch()
                self._store.save(record)

        def emit(kind: EventType, message: str, step_id: str | None = None, **data: Any) -> None:
            bus.publish(
                Event(type=kind, run_id=record.run_id, step_id=step_id, message=message, data=data)
            )

        with self._lock:
            if key in self._active:
                raise RunStateError(f"Run {record.run_id} is already executing.")
            self._active[key] = ctx.cancel_token

        errors: dict[str, BaseException] = {}
        attempts: dict[str, _Attempt] = {}
        pause: tuple[str, float | None] | None = None
        executor = ThreadPoolExecutor(
            max_workers=definition.max_parallel, thread_name_prefix=f"cf-{record.run_id}"
        )
        emit(
            EventType.RUN_STARTED,
            f"{'Started' if started else 'Resumed'} {definition.id}@{definition.version}",
        )
        try:
            while True:
                if ctx.cancel_token.cancelled:
                    break
                if pause is None:
                    try:
                        budget.check_time()
                    except BudgetExceededError as exc:
                        errors.setdefault("__run__", exc)
                        pause = (f"budget exceeded: {exc.limit}", None)
                if pause is None:
                    for step_id in self._schedule(record, running=set(attempts)):
                        if len(attempts) >= definition.max_parallel:
                            break
                        attempts[step_id] = self._launch(executor, record, step_id, ctx, emit)
                    save()
                if not attempts:
                    break
                for step_id, timed_out in self._wait(attempts):
                    attempt = attempts.pop(step_id)
                    hint = self._apply(record, step_id, attempt, timed_out, errors, emit)
                    if hint is not None and pause is None:
                        pause = hint
                save()

            if ctx.cancel_token.cancelled:
                for step_id in list(attempts):
                    attempts.pop(step_id).token.cancel()
                    record.steps[step_id].status = StepStatus.CANCELLED
                    record.steps[step_id].finished_at = _now()
                self._finish_cancelled(record)
            elif pause is not None:
                record.set_status(RunStatus.PAUSED, pause[0])
                record.resume_after = pause[1]
            else:
                self._finish(record)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            with self._lock:
                self._active.pop(key, None)
        emit(
            EventType.RUN_FINISHED,
            f"Run {record.status.value}"
            + (f": {record.status_reason}" if record.status_reason else ""),
            status=record.status.value,
        )
        save()
        return RunResult(record, errors)

    def _schedule(self, record: RunRecord, *, running: set[str]) -> list[str]:
        """Mark skippable steps and return the steps ready to launch, in order."""
        definition = record.definition
        ready: list[str] = []
        for step_id in definition.topological_order():
            state = record.steps[step_id]
            if state.status is not StepStatus.PENDING or step_id in running:
                continue
            step = definition.step(step_id)
            if not step.enabled:
                self._skip(state, SkipReason.DISABLED, "disabled by plan")
                continue
            if any(not record.steps[dep].terminal for dep in step.dependencies()):
                continue
            # Conditions first: a step whose condition is false (e.g. no brand was given)
            # is skipped for that reason, not reported as missing an input.
            if not all(c.evaluate(self._condition_artifact(record, c.source)) for c in step.when):
                self._skip(state, SkipReason.CONDITION, "condition not met")
                continue
            spec = self._registry.spec(step.agent)
            required = {item.name for item in spec.required_inputs}
            missing = [
                f"{name} ({ref})"
                for name, ref in step.inputs.items()
                if name in required and not self._available(record, ref)
            ]
            if missing:
                self._skip(state, SkipReason.DEPENDENCY, "missing input: " + ", ".join(missing))
                continue
            ready.append(step_id)
        return ready

    def _launch(
        self,
        executor: ThreadPoolExecutor,
        record: RunRecord,
        step_id: str,
        ctx: RunContext,
        emit: Callable[..., None],
    ) -> _Attempt:
        step = record.definition.step(step_id)
        spec = self._registry.spec(step.agent)
        inputs = self._step_inputs(record, step)
        state = record.steps[step_id]
        state.status = StepStatus.RUNNING
        state.attempts += 1
        state.started_at = _now()
        state.finished_at = None
        state.input_fingerprint = fingerprint(step, spec, inputs)
        step_ctx = ctx.for_step(step_id)
        timeout = step.timeout_s or spec.limits.timeout_s
        emit(EventType.STEP_STARTED, f"{step_id}: {spec.name}", step_id, attempt=state.attempts)
        future = executor.submit(self._run_step, step, inputs, step_ctx)
        return _Attempt(
            future=future, token=step_ctx.cancel_token, deadline=self._clock() + timeout
        )

    def _wait(self, attempts: Mapping[str, _Attempt]) -> list[tuple[str, bool]]:
        """Wait briefly for progress; return ``(step_id, timed_out)`` for finished steps."""
        now = self._clock()
        nearest = min(a.deadline for a in attempts.values())
        done, _ = wait(
            [a.future for a in attempts.values()],
            timeout=max(0.0, min(nearest - now, _POLL_SECONDS)),
            return_when=FIRST_COMPLETED,
        )
        finished = [(sid, False) for sid, a in attempts.items() if a.future in done]
        if finished:
            return finished
        now = self._clock()
        expired = [(sid, True) for sid, a in attempts.items() if now >= a.deadline]
        for step_id, _ in expired:
            attempts[step_id].token.cancel()
        return expired

    def _apply(
        self,
        record: RunRecord,
        step_id: str,
        attempt: _Attempt,
        timed_out: bool,
        errors: dict[str, BaseException],
        emit: Callable[..., None],
    ) -> tuple[str, float | None] | None:
        """Apply one finished attempt to the record. Returns a pause hint, if any."""
        step = record.definition.step(step_id)
        state = record.steps[step_id]
        exc: BaseException | None
        if timed_out:
            exc = StepTimeoutError(step.agent, f"step {step_id!r} exceeded its time limit")
        else:
            exc = attempt.future.exception()
        state.finished_at = _now()

        if exc is None:
            outcome = attempt.future.result()
            record.artifacts[outcome.artifact.id] = outcome.artifact
            state.artifact_id = outcome.artifact.id
            if outcome.review is not None:
                record.artifacts[outcome.review.id] = outcome.review
                state.review_artifact_id = outcome.review.id
            state.review_score = outcome.score
            state.revisions = outcome.revisions
            state.notes = outcome.notes
            state.error = state.error_type = None
            if step.approval:
                state.status = StepStatus.WAITING_FOR_APPROVAL
                emit(EventType.APPROVAL_REQUESTED, f"{step_id} awaits approval", step_id)
            else:
                state.status = StepStatus.SUCCEEDED
                emit(EventType.STEP_FINISHED, f"{step_id} succeeded", step_id, status="succeeded")
            return None

        errors[step_id] = exc
        if isinstance(exc, RunCancelledError):
            state.status = StepStatus.CANCELLED
            return None
        # Provider errors arrive redacted, but any other exception's text is recorded
        # here, shown in the app and saved in checkpoints: redact it first.
        message = redact(str(exc), self._router.secret_values)
        if isinstance(exc, _PAUSE_ERRORS):
            state.reset()
            state.detail = f"paused: {type(exc).__name__}: {message}"
            emit(EventType.WARNING, f"{step_id} paused: {message}", step_id)
            if isinstance(exc, QuotaExhaustedError):
                return ("quota exhausted", exc.resume_after)
            if isinstance(exc, RateLimitedError):
                return ("rate limited", exc.retry_after)
            return (f"budget exceeded: {exc.limit}", None)
        if state.attempts <= step.retries:
            state.status = StepStatus.PENDING
            state.detail = f"retrying after {type(exc).__name__}"
            emit(EventType.WARNING, f"{step_id} failed, retrying: {message}", step_id)
            return None
        state.status = StepStatus.FAILED
        state.error = message
        state.error_type = type(exc).__name__
        emit(EventType.STEP_FINISHED, f"{step_id} failed: {message}", step_id, status="failed")
        return None

    def _run_step(
        self, step: StepDefinition, inputs: Mapping[str, Artifact], ctx: RunContext
    ) -> _StepOutcome:
        """Run one step (agent plus optional bounded review). Executes in a worker thread."""
        agent = self._registry.create(step.agent)
        output = self._invoke(agent, inputs, ctx)
        notes = list(output.notes)
        review_artifact: Artifact | None = None
        score: float | None = None
        revisions = 0
        if step.review is not None:
            critic = self._registry.create(step.review.critic)
            # The critic also receives any of the step's inputs it declares (e.g. the brief).
            critic_context = {
                item.name: inputs[item.name] for item in critic.spec.inputs if item.name in inputs
            }
            while True:
                verdict = self._invoke(
                    critic, {**critic_context, REVIEW_SUBJECT_INPUT: output.artifact}, ctx
                )
                review_artifact = verdict.artifact
                score = review_artifact.as_model(ReviewResult).score
                if score >= step.review.threshold or revisions >= step.review.max_revisions:
                    break
                ctx.budget.charge_revision()
                revisions += 1
                output = self._invoke(
                    agent, {**inputs, REVIEW_FEEDBACK_INPUT: review_artifact}, ctx
                )
                notes.extend(output.notes)
            if score < step.review.threshold:
                notes.append(
                    f"review score {score:.2f} is below {step.review.threshold:.2f} "
                    f"after {revisions} revision(s)"
                )
        return _StepOutcome(output.artifact, review_artifact, score, revisions, tuple(notes))

    @staticmethod
    def _invoke(agent: Agent, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        ctx.cancel_token.raise_if_cancelled()
        output = agent.run(inputs, ctx.for_invocation())
        expected = agent.spec.output_kind
        if output.artifact.kind != expected:
            raise AgentError(agent.spec.id, f"returned {output.artifact.kind}, expected {expected}")
        return output

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _input_artifacts(
        self, definition: WorkflowDefinition, inputs: Mapping[str, BaseModel | Artifact]
    ) -> dict[str, Artifact]:
        problems: list[str] = []
        missing = sorted(set(definition.inputs) - set(inputs))
        extra = sorted(set(inputs) - set(definition.inputs) - set(definition.optional_inputs))
        if missing:
            problems.append(f"missing workflow input(s): {', '.join(missing)}")
        if extra:
            problems.append(f"unknown workflow input(s): {', '.join(extra)}")
        artifacts: dict[str, Artifact] = {}
        declared = {**definition.optional_inputs, **definition.inputs}
        for name, kind in declared.items():
            value = inputs.get(name)
            if isinstance(value, Artifact):
                if value.kind != kind:
                    problems.append(f"input {name!r} must be {kind}, got {value.kind}")
                artifacts[name] = value
            elif isinstance(value, BaseModel):
                artifacts[name] = Artifact.from_model(
                    kind, value, produced_by=ProducedBy(agent_id="user")
                )
            elif value is not None:
                problems.append(f"input {name!r} must be a Pydantic model or an Artifact")
        if problems:
            raise WorkflowValidationError(problems)
        return artifacts

    @staticmethod
    def _available(record: RunRecord, ref: str) -> bool:
        kind, name = parse_ref(ref)
        if kind == "input":
            return name in record.inputs
        return record.steps[name].status is StepStatus.SUCCEEDED

    def _condition_artifact(self, record: RunRecord, ref: str) -> Artifact | None:
        return record.resolve(ref) if self._available(record, ref) else None

    def _step_inputs(self, record: RunRecord, step: StepDefinition) -> dict[str, Artifact]:
        inputs: dict[str, Artifact] = {}
        for name, ref in step.inputs.items():
            artifact = record.resolve(ref) if self._available(record, ref) else None
            if artifact is not None:
                inputs[name] = artifact
        return inputs

    @staticmethod
    def _skip(state: StepState, reason: SkipReason, detail: str) -> None:
        state.status = StepStatus.SKIPPED
        state.skip_reason = reason
        state.detail = detail
        state.finished_at = _now()

    def _finish(self, record: RunRecord) -> None:
        if any(s.status is StepStatus.WAITING_FOR_APPROVAL for s in record.steps.values()):
            waiting = [
                s.step_id
                for s in record.steps.values()
                if s.status is StepStatus.WAITING_FOR_APPROVAL
            ]
            record.set_status(
                RunStatus.WAITING_FOR_APPROVAL, "awaiting approval: " + ", ".join(waiting)
            )
            return
        problems: list[str] = []
        for step in record.definition.steps:
            state = record.steps[step.id]
            if step.optional:
                continue
            if state.status is StepStatus.FAILED:
                problems.append(f"{step.id} failed")
            elif state.status is StepStatus.SKIPPED and state.skip_reason is SkipReason.DEPENDENCY:
                problems.append(f"{step.id} skipped ({state.detail})")
            elif not state.terminal:
                problems.append(f"{step.id} did not run")
        if problems:
            record.set_status(RunStatus.FAILED, "; ".join(problems))
        else:
            record.set_status(RunStatus.COMPLETED)

    @staticmethod
    def _finish_cancelled(record: RunRecord) -> None:
        for state in record.steps.values():
            if not state.terminal:
                state.status = StepStatus.CANCELLED
                state.finished_at = state.finished_at or _now()
        record.set_status(RunStatus.CANCELLED, "cancelled by user")

    def _cancel_record(self, record: RunRecord, reason: str) -> None:
        for state in record.steps.values():
            if not state.terminal:
                state.status = StepStatus.CANCELLED
                state.finished_at = _now()
        record.set_status(RunStatus.CANCELLED, reason)
        self._publish_direct(record, EventType.RUN_FINISHED, f"Run cancelled: {reason}")
        self._store.save(record)

    def _publish_direct(
        self, record: RunRecord, kind: EventType, message: str, step_id: str | None = None
    ) -> None:
        event = Event(type=kind, run_id=record.run_id, step_id=step_id, message=message)
        record.add_event(event)
        self._events.publish(event)

    def _ensure_inactive(self, record: RunRecord) -> None:
        with self._lock:
            if (record.scope, record.run_id) in self._active:
                raise RunStateError(f"Run {record.run_id} is executing; wait or cancel it.")

    def _prepare_resume(self, record: RunRecord, forced: set[str]) -> None:
        """Reset what must run again; keep every result whose fingerprint still matches."""
        definition = record.definition
        dirty = set(forced)
        for step_id in definition.topological_order():
            state = record.steps[step_id]
            if step_id in dirty:
                continue
            if state.status is StepStatus.SUCCEEDED:
                step = definition.step(step_id)
                spec = self._registry.spec(step.agent)
                current = fingerprint(step, spec, self._step_inputs(record, step))
                if current != state.input_fingerprint:
                    dirty.add(step_id)
            elif state.status is StepStatus.WAITING_FOR_APPROVAL:
                continue
            elif not (
                state.status is StepStatus.SKIPPED
                and state.skip_reason is not SkipReason.DEPENDENCY
            ):
                dirty.add(step_id)
        for step_id in dirty | definition.descendants(dirty):
            record.steps[step_id].reset()
