"""Tests for the 2.0 core: agent specs, registry, budget, events, run context, structured output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from campaign_forge.config import AgentRoute, Settings
from campaign_forge.core import (
    AgentCategory,
    AgentLimits,
    AgentOutput,
    AgentRegistry,
    AgentSpec,
    Budget,
    BudgetLimits,
    CancellationToken,
    Event,
    EventBus,
    EventType,
    InputSpec,
    RunContext,
)
from campaign_forge.domain import Artifact, ProducedBy, TokenUsage
from campaign_forge.exceptions import (
    BudgetExceededError,
    OutputParsingError,
    QuotaExhaustedError,
    RegistryError,
    RunCancelledError,
)
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter, run_structured


def _spec(agent_id: str = "test_agent", **kwargs: Any) -> AgentSpec:
    base: dict[str, Any] = {
        "id": agent_id,
        "name": "Test agent",
        "description": "Used in tests.",
        "category": AgentCategory.CONTENT,
        "output_kind": "test.output@1",
    }
    base.update(kwargs)
    return AgentSpec(**base)


class EchoAgent:
    spec: ClassVar[AgentSpec] = _spec("echo")

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        artifact = Artifact(
            kind=self.spec.output_kind,
            data={"n": len(inputs)},
            produced_by=ProducedBy(agent_id="echo"),
        )
        return AgentOutput(artifact=artifact)


class _Copy(BaseModel):
    tagline: str


# --------------------------------------------------------------------------- #
# AgentSpec
# --------------------------------------------------------------------------- #
def test_agent_spec_validation() -> None:
    spec = _spec(
        inputs=(
            InputSpec(name="brief", kind="input.brief@1"),
            InputSpec(name="research", kind="research.market@1", required=False),
        )
    )
    assert [i.name for i in spec.required_inputs] == ["brief"]
    assert spec.limits == AgentLimits()
    with pytest.raises(ValidationError, match="Duplicate input names"):
        _spec(inputs=(InputSpec(name="a", kind="x.y@1"), InputSpec(name="a", kind="x.z@1")))
    with pytest.raises(ValidationError):
        _spec("Bad-Id")
    with pytest.raises(ValidationError):
        _spec(output_kind="not-a-kind")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_register_lookup_and_create() -> None:
    registry = AgentRegistry()
    assert registry.register(EchoAgent) is EchoAgent
    assert registry.register(EchoAgent) is EchoAgent  # idempotent for the same class
    assert "echo" in registry and len(registry) == 1
    assert registry.get("echo") is EchoAgent
    assert registry.spec("echo").name == "Test agent"
    assert isinstance(registry.create("echo"), EchoAgent)


def test_registry_rejects_invalid_and_duplicate_agents() -> None:
    registry = AgentRegistry()
    registry.register(EchoAgent)

    class Clash:
        spec = _spec("echo")

        def run(self, inputs: Any, ctx: Any) -> Any: ...

    class NoSpec:
        def run(self, inputs: Any, ctx: Any) -> Any: ...

    class NoRun:
        spec = _spec("no_run")

    with pytest.raises(RegistryError, match="already registered"):
        registry.register(Clash)
    with pytest.raises(RegistryError, match="spec"):
        registry.register(NoSpec)
    with pytest.raises(RegistryError, match="run"):
        registry.register(NoRun)
    with pytest.raises(RegistryError, match="Unknown agent"):
        registry.get("missing")


def test_registry_enable_disable_and_listing() -> None:
    registry = AgentRegistry()
    registry.register(EchoAgent)

    class Researcher(EchoAgent):
        spec = _spec("researcher", category=AgentCategory.RESEARCH)

    registry.register(Researcher)
    registry.disable("echo")
    assert not registry.is_enabled("echo")
    with pytest.raises(RegistryError, match="disabled"):
        registry.create("echo")
    assert [s.id for s in registry.specs(include_disabled=False)] == ["researcher"]
    assert [s.id for s in registry.specs(category=AgentCategory.RESEARCH)] == ["researcher"]
    registry.enable("echo")
    assert [s.id for s in registry.specs()] == ["echo", "researcher"]
    registry.unregister("echo")
    assert "echo" not in registry


def _entry_point(name: str, target: Any) -> Any:
    def load() -> Any:
        if isinstance(target, Exception):
            raise target
        return target

    return SimpleNamespace(name=name, load=load)


def test_registry_loads_entry_points() -> None:
    class Hooked(EchoAgent):
        spec = _spec("hooked")

    def hook(registry: AgentRegistry) -> None:
        registry.register(Hooked)

    registry = AgentRegistry()
    points = [_entry_point("class", EchoAgent), _entry_point("hook", hook)]
    loaded = registry.load_entry_points(loader=lambda group: points)
    assert loaded == ["class", "hook"]
    assert "echo" in registry and "hooked" in registry


def test_broken_plugins_fail_loudly_or_are_skipped() -> None:
    points = [_entry_point("broken", ImportError("missing dep")), _entry_point("odd", 42)]
    with pytest.raises(RegistryError, match="broken"):
        AgentRegistry().load_entry_points(loader=lambda group: points)
    registry = AgentRegistry()
    assert registry.load_entry_points(strict=False, loader=lambda group: points) == []


def test_registry_default_loader_scans_real_group() -> None:
    assert AgentRegistry().load_entry_points(group="campaign_forge.tests.none") == []


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_budget_limits_llm_calls() -> None:
    budget = Budget(BudgetLimits(max_llm_calls=2))
    budget.reserve_call()
    budget.reserve_call()
    with pytest.raises(BudgetExceededError) as excinfo:
        budget.reserve_call()
    assert excinfo.value.limit == "llm_calls"


def test_budget_refuses_calls_whose_worst_case_breaches_tokens() -> None:
    budget = Budget(BudgetLimits(max_total_tokens=1000))
    budget.reserve_call(max_completion_tokens=500)
    budget.record_usage(TokenUsage(prompt_tokens=300, completion_tokens=300))
    with pytest.raises(BudgetExceededError) as excinfo:
        budget.reserve_call(max_completion_tokens=500)
    assert excinfo.value.limit == "tokens"
    budget.reserve_call(max_completion_tokens=400)  # 600 + 400 fits exactly


def test_budget_wall_time_revisions_and_repairs() -> None:
    clock = _Clock()
    budget = Budget(BudgetLimits(max_wall_seconds=10, max_revisions=1, max_repairs=1), clock=clock)
    budget.charge_revision()
    with pytest.raises(BudgetExceededError, match="revisions"):
        budget.charge_revision()
    budget.charge_repair()
    with pytest.raises(BudgetExceededError, match="repairs"):
        budget.charge_repair()
    budget.check_time()
    clock.now = 11
    with pytest.raises(BudgetExceededError) as excinfo:
        budget.reserve_call()
    assert excinfo.value.limit == "wall_time"
    snapshot = budget.snapshot()
    assert (snapshot.revisions, snapshot.repairs, snapshot.elapsed_seconds) == (1, 1, 11)
    assert snapshot.total_tokens == 0


# --------------------------------------------------------------------------- #
# Events and cancellation
# --------------------------------------------------------------------------- #
def test_event_bus_isolates_failing_subscribers_and_keeps_history() -> None:
    bus = EventBus(history_size=2)
    seen: list[str] = []

    def broken(event: Event) -> None:
        raise RuntimeError("UI crashed")

    bus.subscribe(broken)
    unsubscribe = bus.subscribe(lambda e: seen.append(e.message))
    for message in ("a", "b", "c"):
        bus.publish(Event(type=EventType.WARNING, run_id="r", message=message))
    assert seen == ["a", "b", "c"]
    assert [e.message for e in bus.history] == ["b", "c"]
    unsubscribe()
    unsubscribe()  # idempotent
    bus.publish(Event(type=EventType.WARNING, run_id="r", message="d"))
    assert seen == ["a", "b", "c"]
    assert EventBus().history == []


def test_cancellation_token() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled
    with pytest.raises(RunCancelledError):
        token.raise_if_cancelled()


# --------------------------------------------------------------------------- #
# RunContext
# --------------------------------------------------------------------------- #
def _context(fake: FakeProvider, **settings_kwargs: Any) -> RunContext:
    settings = Settings(_env_file=None, provider="fake", **settings_kwargs)  # type: ignore[call-arg]
    router = ProviderRouter(settings, providers={"fake": fake})
    return RunContext.create(
        router, limits=BudgetLimits(max_llm_calls=3), events=EventBus(history_size=10)
    )


def test_run_context_complete_budgets_records_and_publishes() -> None:
    fake = FakeProvider("four words of output")
    ctx = _context(fake)
    spec = _spec(limits=AgentLimits(max_tokens=300))
    response = ctx.complete(spec, CompletionRequest.from_prompts("s", "u"), step_id="step-1")
    assert response.text == "four words of output"
    sent = fake.requests[0]
    assert sent.max_tokens == 300  # spec default applied
    assert sent.metadata["agent"] == spec.id and sent.metadata["run_id"] == ctx.run_id
    snapshot = ctx.budget.snapshot()
    assert snapshot.llm_calls == 1 and snapshot.usage.completion_tokens == 4
    [event] = ctx.events.history
    assert event.type is EventType.LLM_CALL_FINISHED and event.step_id == "step-1"


def test_configured_agent_max_tokens_beats_spec_default() -> None:
    fake = FakeProvider("ok")
    ctx = _context(fake, agent_overrides={"test_agent": AgentRoute(max_tokens=1500)})
    ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))
    assert fake.requests[0].max_tokens == 1500
    ctx.overrides["test_agent"] = AgentRoute(max_tokens=50)
    ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))
    assert fake.requests[1].max_tokens == 50


def test_run_context_respects_cancellation_and_budget() -> None:
    ctx = _context(FakeProvider("ok"))
    for _ in range(3):
        ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))
    with pytest.raises(BudgetExceededError):
        ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))
    ctx.cancel_token.cancel()
    with pytest.raises(RunCancelledError):
        ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))


def test_run_context_publishes_fallback_event() -> None:
    primary = FakeProvider(failures=[QuotaExhaustedError("out")])
    backup = FakeProvider("backup", name="groq")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, provider="fake", groq_api_key="k" * 20, fallback_providers="groq"
    )
    router = ProviderRouter(settings, providers={"fake": primary, "groq": backup})
    ctx = RunContext.create(router, events=EventBus(history_size=10))
    response = ctx.complete(_spec(), CompletionRequest.from_prompts("s", "u"))
    assert response.fallback_from == "fake"
    assert [e.type for e in ctx.events.history] == [
        EventType.LLM_CALL_FINISHED,
        EventType.PROVIDER_FALLBACK,
    ]


def test_run_context_structured_output_charges_repairs() -> None:
    answers = iter(["not json", json.dumps({"tagline": "Sip smart"})])
    fake = FakeProvider(lambda request: next(answers))
    ctx = _context(fake)
    result = ctx.complete_structured(_spec(), CompletionRequest.from_prompts("s", "u"), _Copy)
    assert result.value.tagline == "Sip smart"
    assert result.repairs == 1
    assert ctx.budget.snapshot().repairs == 1
    repair_request = fake.requests[1]
    assert [m.role for m in repair_request.messages] == ["system", "user", "assistant", "user"]
    assert repair_request.json_mode is True


# --------------------------------------------------------------------------- #
# Structured output (provider level)
# --------------------------------------------------------------------------- #
def test_provider_structured_output_success_and_usage() -> None:
    fake = FakeProvider(json.dumps({"tagline": "Hello there"}))
    result = fake.complete_structured(CompletionRequest.from_prompts("s", "u"), _Copy)
    assert result.value.tagline == "Hello there"
    assert result.repairs == 0
    assert result.usage.completion_tokens == 3


def test_structured_output_gives_up_after_max_repairs() -> None:
    fake = FakeProvider("never json")
    request = CompletionRequest.from_prompts("s", "u", metadata={"agent": "copywriter"})
    with pytest.raises(OutputParsingError) as excinfo:
        fake.complete_structured(request, _Copy, max_repairs=2)
    assert excinfo.value.agent == "copywriter"
    assert fake.call_count == 3
    with pytest.raises(ValueError, match="max_repairs"):
        run_structured(fake.complete, request, _Copy, max_repairs=9)


def test_truncated_answers_get_one_concise_retry() -> None:
    from campaign_forge.exceptions import TruncatedResponseError

    fake = FakeProvider("short answer", failures=[TruncatedResponseError("cut")])
    ctx = _context(fake)
    response = ctx.complete(_spec(), CompletionRequest.from_prompts("s", "write a lot"))
    assert response.text == "short answer"
    retry = fake.requests[1].messages[-1].content
    assert "cut off at the length limit" in retry and "write a lot" in retry
    assert len(fake.requests[1].messages) == 2  # still system + user (v1 clients accept it)
    assert ctx.budget.snapshot().repairs == 1
    assert any("truncated" in e.message for e in ctx.events.history)
    twice = FakeProvider(
        "x", failures=[TruncatedResponseError("cut"), TruncatedResponseError("cut")]
    )
    with pytest.raises(TruncatedResponseError):
        _context(twice).complete(_spec(), CompletionRequest.from_prompts("s", "u"))
