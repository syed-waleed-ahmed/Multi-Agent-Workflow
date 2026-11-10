"""Behavioural tests for the workflow engine (every rule in docs/ARCHITECTURE.md T5 / T6)."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import pytest

from campaign_forge.core import (
    AgentLimits,
    AgentOutput,
    BudgetLimits,
    EventType,
    InputSpec,
    RunContext,
)
from campaign_forge.domain import Artifact, ProducedBy, Scope
from campaign_forge.exceptions import (
    AgentError,
    LLMError,
    QuotaExhaustedError,
    RateLimitedError,
    RunStateError,
    StepTimeoutError,
    WorkflowValidationError,
)
from campaign_forge.orchestration import (
    Condition,
    ReviewSpec,
    RunStatus,
    SkipReason,
    StepStatus,
)
from campaign_forge.providers import FakeProvider

from .orchestration_helpers import (
    INPUT_KIND,
    TEXT_KIND,
    Text,
    make_agent,
    make_critic,
    make_engine,
    step,
    topic,
    workflow,
)

TOPIC = InputSpec(name="topic", kind=INPUT_KIND)
PRIOR = InputSpec(name="prior", kind=TEXT_KIND)
OPTIONAL_PRIOR = InputSpec(name="prior", kind=TEXT_KIND, required=False)


def _fails(message: str = "boom") -> object:
    def behaviour(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        raise LLMError(message)

    return behaviour


# --------------------------------------------------------------------------- #
# Happy path, outputs, usage, events
# --------------------------------------------------------------------------- #
def test_linear_workflow_completes_with_outputs_usage_and_events() -> None:
    first = make_agent("first", inputs=(TOPIC,))
    second = make_agent("second", inputs=(PRIOR,))
    engine, fake = make_engine(first, second, provider=FakeProvider("hello world"))
    seen: list[EventType] = []
    engine.events.subscribe(lambda e: seen.append(e.type))
    definition = workflow(
        step("a", "first", inputs={"topic": "input:topic"}),
        step("b", "second", inputs={"prior": "step:a"}),
        outputs={"final": "step:b"},
    )
    result = engine.start(definition, topic())
    assert result.ok and result.status is RunStatus.COMPLETED
    assert result.output("final", Text).text == "hello world"
    assert result.record.llm_calls == 2
    assert result.record.usage.completion_tokens == 4
    assert second.calls[0]["prior"].as_model(Text).text == "hello world"
    assert seen[0] is EventType.RUN_STARTED and seen[-1] is EventType.RUN_FINISHED
    assert EventType.LLM_CALL_FINISHED in seen
    stored = engine.get(result.record.run_id)
    assert stored.status is RunStatus.COMPLETED and len(engine.list_runs()) == 1
    with pytest.raises(KeyError, match="not available"):
        result.output("missing", Text)


def test_independent_steps_run_in_parallel() -> None:
    barrier = threading.Barrier(2, timeout=5)

    def meet(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        barrier.wait()  # deadlocks (and times out) unless both run at once
        return "met"

    left = make_agent("left", behaviour=meet)
    right = make_agent("right", behaviour=meet)
    engine, _ = make_engine(left, right)
    result = engine.start(workflow(step("l", "left"), step("r", "right"), max_parallel=2), topic())
    assert result.ok


def test_failure_is_isolated_to_dependants() -> None:
    bad = make_agent("bad", behaviour=_fails())  # type: ignore[arg-type]
    good = make_agent("good")
    after_bad = make_agent("after_bad", inputs=(PRIOR,))
    engine, _ = make_engine(bad, good, after_bad)
    definition = workflow(
        step("bad", "bad"),
        step("good", "good"),
        step("downstream", "after_bad", inputs={"prior": "step:bad"}),
        outputs={"good": "step:good"},
    )
    result = engine.start(definition, topic())
    steps = result.record.steps
    assert result.status is RunStatus.FAILED
    assert steps["bad"].status is StepStatus.FAILED and steps["bad"].error_type == "LLMError"
    assert steps["good"].status is StepStatus.SUCCEEDED
    assert steps["downstream"].skip_reason is SkipReason.DEPENDENCY
    assert "good" in result.outputs()  # partial results survive
    assert isinstance(result.first_error(), LLMError)


def test_optional_inputs_and_optional_steps() -> None:
    bad = make_agent("bad", behaviour=_fails())  # type: ignore[arg-type]
    tolerant = make_agent("tolerant", inputs=(OPTIONAL_PRIOR,))
    engine, _ = make_engine(bad, tolerant)
    definition = workflow(
        step("bad", "bad", optional=True),
        step("tolerant", "tolerant", inputs={"prior": "step:bad"}),
    )
    result = engine.start(definition, topic())
    assert result.ok  # the optional failure does not fail the run
    assert "prior" not in tolerant.calls[0]


def test_conditions_and_disabled_steps() -> None:
    agent = make_agent("agent")
    engine, _ = make_engine(agent)
    definition = workflow(
        step(
            "yes",
            "agent",
            when=(Condition(source="input:topic", path="text", op="equals", value="bottles"),),
        ),
        step(
            "no",
            "agent",
            when=(Condition(source="input:topic", path="text", op="equals", value="cars"),),
        ),
        step("off", "agent", optional=True, enabled=False),
    )
    result = engine.start(definition, topic())
    steps = result.record.steps
    assert result.ok
    assert steps["yes"].status is StepStatus.SUCCEEDED
    assert steps["no"].skip_reason is SkipReason.CONDITION
    assert steps["off"].skip_reason is SkipReason.DISABLED


# --------------------------------------------------------------------------- #
# Retries, timeouts, call limits, wrong output
# --------------------------------------------------------------------------- #
def test_retries_recover_a_flaky_step() -> None:
    attempts = {"n": 0}

    def flaky(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise LLMError("transient")
        return "fine"

    engine, _ = make_engine(make_agent("flaky", behaviour=flaky))
    result = engine.start(workflow(step("f", "flaky", retries=1)), topic())
    assert result.ok and result.record.steps["f"].attempts == 2


def test_step_timeout_fails_the_step_and_cancels_its_token() -> None:
    def slow(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        for _ in range(100):
            ctx.cancel_token.raise_if_cancelled()
            time.sleep(0.02)
        return "too late"

    engine, _ = make_engine(make_agent("slow", behaviour=slow))
    started = time.monotonic()
    result = engine.start(workflow(step("s", "slow", timeout_s=0.1)), topic())
    assert time.monotonic() - started < 1.5
    assert result.record.steps["s"].error_type == "StepTimeoutError"
    assert isinstance(result.errors["s"], StepTimeoutError)


def test_agent_call_limit_and_wrong_output_kind() -> None:
    def chatty(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        from campaign_forge.providers import CompletionRequest

        for _ in range(3):
            ctx.complete(Chatty.spec, CompletionRequest.from_prompts("s", "u"))
        return "never"

    Chatty = make_agent("chatty", behaviour=chatty, limits=AgentLimits(max_calls=2))

    class WrongKind:
        spec = make_agent("wrong").spec

        def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
            artifact = Artifact(kind="other.kind@1", data={}, produced_by=ProducedBy(agent_id="w"))
            return AgentOutput(artifact=artifact)

    engine, _ = make_engine(Chatty, WrongKind)
    result = engine.start(workflow(step("c", "chatty"), step("w", "wrong")), topic())
    assert "limit of 2 model calls" in (result.record.steps["c"].error or "")
    assert isinstance(result.errors["w"], AgentError)
    assert "expected test.text@1" in (result.record.steps["w"].error or "")


# --------------------------------------------------------------------------- #
# Pause and resume
# --------------------------------------------------------------------------- #
def test_quota_exhaustion_pauses_and_resume_skips_completed_steps() -> None:
    first = make_agent("first")
    second = make_agent("second", inputs=(PRIOR,))
    fake = FakeProvider("ok")
    engine, _ = make_engine(first, second, provider=fake)
    definition = workflow(step("a", "first"), step("b", "second", inputs={"prior": "step:a"}))
    original = fake.complete
    calls = {"n": 0}

    def complete_with_quota(request):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise QuotaExhaustedError("tokens per day", resume_after=30.0)
        return original(request)

    fake.complete = complete_with_quota  # type: ignore[method-assign]
    paused = engine.start(definition, topic())
    record = paused.record
    assert paused.status is RunStatus.PAUSED and record.status_reason == "quota exhausted"
    assert record.resume_after == 30.0
    assert record.steps["a"].status is StepStatus.SUCCEEDED
    assert record.steps["b"].status is StepStatus.PENDING
    resumed = engine.resume(record.run_id)
    assert resumed.ok
    assert calls["n"] == 3  # step a was not repeated
    assert resumed.record.llm_calls == 3  # every attempted call counts, including the refused one


def test_long_rate_limit_pauses_with_hint() -> None:
    fake = FakeProvider(failures=[RateLimitedError("slow down", retry_after=400.0)])
    engine, _ = make_engine(make_agent("a"), provider=fake)
    result = engine.start(workflow(step("a", "a")), topic())
    assert result.status is RunStatus.PAUSED
    assert (result.record.status_reason, result.record.resume_after) == ("rate limited", 400.0)


def test_budget_exhaustion_pauses_and_new_limits_resume() -> None:
    engine, _ = make_engine(make_agent("a"), make_agent("b"))
    definition = workflow(
        step("a", "a"), step("b", "b", after=("a",)), limits=BudgetLimits(max_llm_calls=1)
    )
    paused = engine.start(definition, topic())
    assert paused.status is RunStatus.PAUSED
    assert paused.record.status_reason == "budget exceeded: llm_calls"
    resumed = engine.resume(paused.record.run_id, limits=BudgetLimits(max_llm_calls=5))
    assert resumed.ok


def test_wall_clock_budget_pauses_between_steps() -> None:
    clock = {"now": 0.0}

    def advance(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        clock["now"] += 100
        return "x"

    engine, _ = make_engine(make_agent("a", behaviour=advance), make_agent("b", behaviour=advance))
    engine._clock = lambda: clock["now"]  # noqa: SLF001
    definition = workflow(
        step("a", "a"), step("b", "b", after=("a",)), limits=BudgetLimits(max_wall_seconds=50)
    )
    result = engine.start(definition, topic())
    assert result.status is RunStatus.PAUSED
    assert result.record.status_reason == "budget exceeded: wall_time"


def test_resume_reruns_forced_steps_and_their_dependants() -> None:
    first, second = make_agent("first"), make_agent("second", inputs=(PRIOR,))
    engine, fake = make_engine(first, second)
    definition = workflow(step("a", "first"), step("b", "second", inputs={"prior": "step:a"}))
    done = engine.start(definition, topic())
    record = engine.get(done.record.run_id)
    record.status = RunStatus.FAILED  # pretend a later failure so resume is allowed
    engine.store.save(record)
    again = engine.resume(record.run_id, rerun=["a"])
    assert again.ok and fake.call_count == 4  # both steps re-ran
    with pytest.raises(RunStateError, match="completed"):
        engine.resume(record.run_id)
    record = engine.get(record.run_id)
    record.status = RunStatus.FAILED
    engine.store.save(record)
    with pytest.raises(RunStateError, match="Unknown step"):
        engine.resume(record.run_id, rerun=["zz"])


def test_resume_detects_agent_version_changes_and_interrupted_runs() -> None:
    v1 = make_agent("agent", version="1.0.0")
    engine, fake = make_engine(v1)
    definition = workflow(step("a", "agent"))
    result = engine.start(definition, topic())
    record = engine.get(result.record.run_id)
    record.status = RunStatus.RUNNING  # simulate a crash mid-run
    record.steps["a"].status = StepStatus.SUCCEEDED
    engine.store.save(record)
    v2 = make_agent("agent", version="2.0.0")
    engine2, fake2 = make_engine(v2, store=engine.store)
    resumed = engine2.resume(record.run_id)
    assert resumed.ok and fake2.call_count == 1  # fingerprint changed -> re-ran


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_cancelling_an_executing_run() -> None:
    release = threading.Event()

    def blocking(inputs: Mapping[str, Artifact], ctx: RunContext) -> str:
        while not release.wait(0.02):
            ctx.cancel_token.raise_if_cancelled()
        return "x"

    engine, _ = make_engine(make_agent("block", behaviour=blocking), make_agent("next"))
    definition = workflow(step("a", "block"), step("b", "next", after=("a",)))

    def cancel_when_started(event: object) -> None:
        if getattr(event, "type", None) is EventType.STEP_STARTED:
            engine.cancel(event.run_id)  # type: ignore[attr-defined]

    engine.events.subscribe(cancel_when_started)
    result = engine.start(definition, topic())
    release.set()
    assert result.status is RunStatus.CANCELLED
    assert {s.status for s in result.record.steps.values()} == {StepStatus.CANCELLED}


def test_cancelling_stored_runs() -> None:
    engine, _ = make_engine(make_agent("a"))
    paused = engine.start(
        workflow(step("a", "a"), limits=BudgetLimits(max_llm_calls=1, max_total_tokens=10)), topic()
    )
    assert paused.status is RunStatus.PAUSED
    cancelled = engine.cancel(paused.record.run_id)
    assert cancelled.status is RunStatus.CANCELLED
    with pytest.raises(RunStateError, match="already cancelled"):
        engine.cancel(paused.record.run_id)


# --------------------------------------------------------------------------- #
# Approval gates
# --------------------------------------------------------------------------- #
def _approval_flow() -> tuple[object, object]:
    draft = make_agent("draft")
    publish = make_agent("publish", inputs=(PRIOR,))
    other = make_agent("other")
    engine, _ = make_engine(draft, publish, other)
    definition = workflow(
        step("draft", "draft", approval=True),
        step("publish", "publish", inputs={"prior": "step:draft"}),
        step("side", "other"),
        outputs={"final": "step:publish"},
    )
    return engine, publish, definition  # type: ignore[return-value]


def test_approval_blocks_dependants_but_not_other_branches() -> None:
    engine, publish, definition = _approval_flow()  # type: ignore[misc]
    waiting = engine.start(definition, topic())
    steps = waiting.record.steps
    assert waiting.status is RunStatus.WAITING_FOR_APPROVAL
    assert steps["draft"].status is StepStatus.WAITING_FOR_APPROVAL
    assert steps["publish"].status is StepStatus.PENDING
    assert steps["side"].status is StepStatus.SUCCEEDED
    with pytest.raises(RunStateError, match="waiting for approval"):
        engine.resume(waiting.record.run_id)
    with pytest.raises(RunStateError, match="not waiting"):
        engine.approve(waiting.record.run_id, "side")
    done = engine.approve(
        waiting.record.run_id, "draft", edited_data={"text": "edited by a human"}, comment="LGTM"
    )
    assert done.ok
    assert publish.calls[-1]["prior"].as_model(Text).text == "edited by a human"
    assert "edited by reviewer" in done.record.steps["draft"].notes


def test_rejection_cancels_the_run() -> None:
    engine, _, definition = _approval_flow()  # type: ignore[misc]
    waiting = engine.start(definition, topic())
    rejected = engine.approve(waiting.record.run_id, "draft", approved=False, comment="off-brand")
    assert rejected.status is RunStatus.CANCELLED
    assert rejected.record.steps["draft"].error == "off-brand"
    assert rejected.record.steps["publish"].status is StepStatus.CANCELLED


# --------------------------------------------------------------------------- #
# Review loop
# --------------------------------------------------------------------------- #
def test_review_loop_revises_until_threshold() -> None:
    writer = make_agent("writer", feedback=True)
    critic = make_critic([0.3, 0.9])
    engine, _ = make_engine(writer, critic)
    definition = workflow(
        step("w", "writer", review=ReviewSpec(critic="critic", threshold=0.8, max_revisions=2))
    )
    result = engine.start(definition, topic())
    state = result.record.steps["w"]
    assert result.ok and state.revisions == 1 and state.review_score == 0.9
    assert "feedback" in writer.calls[1]
    assert state.review_artifact_id in result.record.artifacts


def test_review_loop_is_bounded_and_noted() -> None:
    writer = make_agent("writer", feedback=True)
    engine, _ = make_engine(writer, make_critic([0.1]))
    definition = workflow(
        step("w", "writer", review=ReviewSpec(critic="critic", threshold=0.8, max_revisions=1))
    )
    result = engine.start(definition, topic())
    state = result.record.steps["w"]
    assert result.ok and state.revisions == 1 and len(writer.calls) == 2
    assert any("below 0.80" in note for note in state.notes)


# --------------------------------------------------------------------------- #
# Validation before any run exists
# --------------------------------------------------------------------------- #
def test_start_rejects_invalid_definitions_and_inputs() -> None:
    engine, _ = make_engine(make_agent("a", inputs=(TOPIC,)))
    with pytest.raises(WorkflowValidationError, match="unknown agent"):
        engine.start(workflow(step("x", "ghost")), topic())
    definition = workflow(step("a", "a", inputs={"topic": "input:topic"}))
    with pytest.raises(WorkflowValidationError, match="missing workflow input"):
        engine.start(definition, {})
    with pytest.raises(WorkflowValidationError, match="unknown workflow input"):
        engine.start(definition, {**topic(), "extra": Text(text="x")})
    wrong = Artifact(kind=TEXT_KIND, data={"text": "x"}, produced_by=ProducedBy(agent_id="u"))
    with pytest.raises(WorkflowValidationError, match="must be test.input@1"):
        engine.start(definition, {"topic": wrong})
    with pytest.raises(WorkflowValidationError, match="Pydantic model or an Artifact"):
        engine.start(definition, {"topic": "plain string"})  # type: ignore[dict-item]
    assert engine.list_runs(scope=Scope.local()) == []
