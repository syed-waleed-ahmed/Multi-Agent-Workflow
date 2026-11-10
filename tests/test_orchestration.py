"""Tests for workflow definitions, run state, run stores, planners and templates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from campaign_forge.config import Settings
from campaign_forge.core import AgentRegistry, InputSpec
from campaign_forge.core.events import Event, EventType
from campaign_forge.domain import Artifact, ProducedBy, Scope
from campaign_forge.exceptions import (
    ConfigurationError,
    ProviderAuthError,
    RunNotFoundError,
    RunStateError,
    WorkflowValidationError,
)
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import (
    BUILTIN_TEMPLATES,
    Condition,
    InMemoryRunStore,
    JsonFileRunStore,
    LLMPlanner,
    ReviewSpec,
    RuleBasedPlanner,
    RunRecord,
    RunStatus,
    StepStatus,
    TemplateInfo,
    classic_campaign,
    default_registry,
    get_template,
    load_definition,
    parse_ref,
    validate_definition,
)
from campaign_forge.orchestration.state import MAX_EVENTS
from campaign_forge.providers import FakeProvider, ProviderRouter

from .orchestration_helpers import (
    INPUT_KIND,
    TEXT_KIND,
    Text,
    make_agent,
    make_critic,
    step,
    workflow,
)

TOPIC = InputSpec(name="topic", kind=INPUT_KIND)
PRIOR = InputSpec(name="prior", kind=TEXT_KIND)


def _brief() -> CampaignBrief:
    return CampaignBrief(
        product_name="EcoSip",
        product_description="Insulated bottle.",
        target_audience="hikers",
        goal="sales",
        tone="fun",
        channels=["email", "Instagram"],
    )


# --------------------------------------------------------------------------- #
# Definitions
# --------------------------------------------------------------------------- #
def test_parse_ref() -> None:
    assert parse_ref("input:brief") == ("input", "brief")
    assert parse_ref("step:copy_2") == ("step", "copy_2")
    with pytest.raises(ValueError, match="Invalid reference"):
        parse_ref("brief")


@pytest.mark.parametrize(
    "steps,message",
    [
        ((step("a", "x"), step("a", "y")), "duplicate step ids"),
        ((step("a", "x", inputs={"t": "input:missing"}),), "unknown input 'missing'"),
        ((step("a", "x", inputs={"t": "step:ghost"}),), "unknown step 'ghost'"),
        ((step("a", "x", after=("ghost",)),), "waits for unknown step"),
        ((step("a", "x", after=("a",)),), "depends on itself"),
        ((step("a", "x", enabled=False),), "disabled but not optional"),
        (
            (
                step("a", "x", after=("b",)),
                step("b", "x", after=("c",)),
                step("c", "x", after=("a",)),
            ),
            "dependency cycle: a -> b -> c -> a",
        ),
        (
            (step("a", "x", when=(Condition(source="step:zz", op="exists"),)),),
            "condition refers to unknown step",
        ),
    ],
)
def test_structural_validation(steps: tuple[Any, ...], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        workflow(*steps)


def test_unknown_output_reference_rejected() -> None:
    with pytest.raises(ValidationError, match="output 'final'"):
        workflow(step("a", "x"), outputs={"final": "step:nope"})


def test_topological_order_descendants_and_disabling() -> None:
    definition = workflow(
        step("c", "x", inputs={"p": "step:b"}),
        step("a", "x"),
        step("b", "x", inputs={"p": "step:a"}),
        step("d", "x", optional=True),
    )
    order = definition.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")
    assert definition.descendants({"a"}) == {"b", "c"}
    disabled = definition.with_disabled(["d"])
    assert disabled.step("d").enabled is False
    with pytest.raises(KeyError):
        definition.step("zz")


def test_load_definition_from_json_string_and_file(tmp_path: Path) -> None:
    definition = classic_campaign()
    text = definition.model_dump_json()
    assert load_definition(text) == definition
    path = tmp_path / "flow.json"
    path.write_text(text, encoding="utf-8")
    assert load_definition(path) == definition
    with pytest.raises(WorkflowValidationError, match="steps"):
        load_definition(json.dumps({"id": "x", "name": "x", "inputs": {}, "steps": []}))
    with pytest.raises(WorkflowValidationError, match="cannot read"):
        load_definition(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "op,value,data,expected",
    [
        ("exists", None, {"a": 1}, True),
        ("not_exists", None, {"b": 1}, True),
        ("equals", 1, {"a": 1}, True),
        ("not_equals", 1, {"a": 2}, True),
        ("contains", "INSTAGRAM", {"a": ["email", "instagram"]}, True),
        ("contains", "INSTA", {"a": ["email", "instagram"]}, False),
        ("contains", "gram", {"a": "Instagram ads"}, True),
        ("not_contains", "tiktok", {"a": ["email"]}, True),
        ("contains", "k", {"a": {"k": 1}}, True),
        ("contains", "x", {"a": 5}, False),
        ("truthy", None, {"a": [1]}, True),
        ("falsy", None, {"a": []}, True),
    ],
)
def test_condition_operators(op: str, value: Any, data: dict[str, Any], expected: bool) -> None:
    artifact = Artifact(kind=TEXT_KIND, data=data, produced_by=ProducedBy(agent_id="t"))
    condition = Condition(source="input:topic", path="a", op=op, value=value)  # type: ignore[arg-type]
    assert condition.evaluate(artifact) is expected


def test_condition_on_missing_artifact_or_path() -> None:
    artifact = Artifact(kind=TEXT_KIND, data={"a": {"b": 1}}, produced_by=ProducedBy(agent_id="t"))
    assert Condition(source="input:t", path="a.b", op="equals", value=1).evaluate(artifact)
    assert not Condition(source="input:t", path="a.z", op="exists").evaluate(artifact)
    assert Condition(source="input:t", op="not_exists").evaluate(None)
    assert Condition(source="input:t", op="falsy").evaluate(None)


def _registry(*agents: type) -> AgentRegistry:
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    return registry


def test_validate_definition_reports_every_problem() -> None:
    writer = make_agent("writer", inputs=(TOPIC,))
    reader = make_agent("reader", inputs=(PRIOR,))
    disabled = make_agent("off")
    registry = _registry(writer, reader, disabled)
    registry.disable("off")
    definition = workflow(
        step("w", "writer", inputs={"bogus": "input:topic"}),
        step("r", "reader", inputs={"prior": "input:topic"}),
        step("o", "off"),
        step("g", "ghost"),
    )
    with pytest.raises(WorkflowValidationError) as excinfo:
        validate_definition(definition, registry)
    text = "\n".join(excinfo.value.problems)
    assert "has no input 'bogus'" in text
    assert "required input 'topic' is not bound" in text
    assert "expects test.text@1, but input:topic provides test.input@1" in text
    assert "agent 'off' is disabled" in text
    assert "unknown agent 'ghost'" in text


def test_validate_review_wiring() -> None:
    plain = make_agent("plain", inputs=(TOPIC,))
    reviewable = make_agent("reviewable", inputs=(TOPIC,), feedback=True)
    not_a_critic = make_agent("not_critic")
    registry = _registry(plain, reviewable, not_a_critic, make_critic([1.0]))
    bad = workflow(
        step("a", "plain", inputs={"topic": "input:topic"}, review=ReviewSpec(critic="critic")),
        step(
            "b",
            "reviewable",
            inputs={"topic": "input:topic"},
            review=ReviewSpec(critic="not_critic"),
        ),
        step(
            "c", "reviewable", inputs={"topic": "input:topic"}, review=ReviewSpec(critic="nobody")
        ),
    )
    with pytest.raises(WorkflowValidationError) as excinfo:
        validate_definition(bad, registry)
    text = "\n".join(excinfo.value.problems)
    assert "needs an optional 'feedback' input" in text
    assert "critic 'not_critic' must produce quality.review@1" in text
    assert "unknown agent 'nobody'" in text
    good = workflow(
        step("b", "reviewable", inputs={"topic": "input:topic"}, review=ReviewSpec(critic="critic"))
    )
    validate_definition(good, registry)


def test_classic_template_is_valid_for_default_registry() -> None:
    validate_definition(classic_campaign(), default_registry())
    assert get_template("classic-campaign").id == "classic-campaign"
    with pytest.raises(ConfigurationError, match="Unknown workflow"):
        get_template("nope")


# --------------------------------------------------------------------------- #
# Run state
# --------------------------------------------------------------------------- #
def _record(**kwargs: Any) -> RunRecord:
    definition = workflow(step("a", "x", inputs={"t": "input:topic"}), outputs={"out": "step:a"})
    topic = Artifact(kind=INPUT_KIND, data={"text": "t"}, produced_by=ProducedBy(agent_id="user"))
    return RunRecord.new(
        run_id="run-test",
        scope=Scope.local(),
        definition=definition,
        inputs={"topic": topic},
        limits=definition.limits,
        **kwargs,
    )


def test_run_state_transitions() -> None:
    record = _record()
    record.set_status(RunStatus.PLANNING)
    record.set_status(RunStatus.RUNNING)
    record.set_status(RunStatus.PAUSED, "quota")
    record.resume_after = 30
    record.set_status(RunStatus.RUNNING)
    assert record.resume_after is None
    record.set_status(RunStatus.COMPLETED)
    with pytest.raises(RunStateError, match="cannot move from completed"):
        record.set_status(RunStatus.RUNNING)


def test_outputs_only_include_succeeded_steps_and_events_are_capped() -> None:
    record = _record()
    artifact = Artifact(kind=TEXT_KIND, data={"text": "x"}, produced_by=ProducedBy(agent_id="x"))
    record.artifacts[artifact.id] = artifact
    record.steps["a"].artifact_id = artifact.id
    record.steps["a"].status = StepStatus.WAITING_FOR_APPROVAL
    assert record.outputs() == {}
    record.steps["a"].status = StepStatus.SUCCEEDED
    assert record.outputs()["out"] == artifact
    assert record.resolve("input:topic") is not None
    for i in range(MAX_EVENTS + 5):
        record.add_event(Event(type=EventType.WARNING, run_id="run-test", message=str(i)))
    assert len(record.events) == MAX_EVENTS
    assert record.events[-1].message == str(MAX_EVENTS + 4)
    summary = record.summary()
    assert summary["steps"] == {"a": "succeeded"} and summary["workflow"] == "test-flow@1.0.0"


# --------------------------------------------------------------------------- #
# Stores
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["memory", "file"])
def test_store_round_trip_and_scope_isolation(kind: str, tmp_path: Path) -> None:
    store = InMemoryRunStore() if kind == "memory" else JsonFileRunStore(tmp_path)
    record = _record()
    store.save(record)
    assert store.load("run-test", Scope.local()).run_id == "run-test"
    other = Scope(org_id="acme", user_id="u1")
    with pytest.raises(RunNotFoundError):
        store.load("run-test", other)
    assert [r.run_id for r in store.list_runs(Scope.local())] == ["run-test"]
    assert store.list_runs(other) == []
    store.delete("run-test", Scope.local())
    with pytest.raises(RunNotFoundError):
        store.delete("run-test", Scope.local())


def test_file_store_encodes_scope_and_rejects_bad_ids(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path / "runs")
    weird = Scope(org_id="..", user_id="a:b")
    record = _record().model_copy(update={"scope": weird})
    store.save(record)
    files = list((tmp_path / "runs").rglob("*.json"))
    assert len(files) == 1
    assert (tmp_path / "runs") in files[0].parents  # never escapes the root
    (files[0].parent / ".tmp-x.json").write_text("{}", encoding="utf-8")
    assert len(store.list_runs(weird)) == 1  # temp files ignored
    for bad in ("../x", "a/b", "", "x" * 80):
        with pytest.raises(RunNotFoundError, match="Invalid run id"):
            store.load(bad, weird)


# --------------------------------------------------------------------------- #
# Planners
# --------------------------------------------------------------------------- #
def _optional_template() -> TemplateInfo:
    def build() -> Any:
        return workflow(step("a", "x"), step("b", "x", optional=True))

    return TemplateInfo("flex", "Flex", "Has an optional step", build)


def test_rule_based_planner() -> None:
    planner = RuleBasedPlanner()
    plan = planner.plan(_brief())
    assert plan.definition.id == "full-campaign"
    assert plan.rationale == ("full-campaign: default template",)
    flex = RuleBasedPlanner({"flex": _optional_template()})
    plan = flex.plan(_brief(), template="flex", disabled_steps=["b"])
    assert plan.definition.step("b").enabled is False
    with pytest.raises(ConfigurationError, match="Only optional steps"):
        flex.plan(_brief(), template="flex", disabled_steps=["a"])
    with pytest.raises(ConfigurationError, match="Unknown workflow"):
        planner.plan(_brief(), template="nope")


def _router(responder: Any) -> ProviderRouter:
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    return ProviderRouter(settings, providers={"fake": FakeProvider(responder)})


def test_llm_planner_uses_valid_proposals() -> None:
    templates = {"flex": _optional_template(), **BUILTIN_TEMPLATES}
    proposal = {"template": "flex", "disabled_steps": ["b"], "rationale": "Short brief."}
    planner = LLMPlanner(_router(json.dumps(proposal)), templates)
    plan = planner.plan(_brief())
    assert plan.planner == "llm"
    assert plan.definition.id == "test-flow"
    assert plan.definition.step("b").enabled is False
    assert "Short brief." in plan.rationale


@pytest.mark.parametrize(
    "responder",
    [
        json.dumps({"template": "made-up"}),
        json.dumps({"template": "classic-campaign", "disabled_steps": ["research"]}),
        "not json at all",
    ],
)
def test_llm_planner_falls_back_on_invalid_proposals(responder: str) -> None:
    plan = LLMPlanner(_router(responder)).plan(_brief())
    assert plan.planner == "rules (fallback)"
    assert plan.definition.id == "full-campaign"
    assert plan.rationale[-1].startswith("LLM planner unavailable")


def test_llm_planner_falls_back_on_provider_errors() -> None:
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    failing = FakeProvider(failures=[ProviderAuthError("bad key")])
    plan = LLMPlanner(ProviderRouter(settings, providers={"fake": failing})).plan(_brief())
    assert plan.planner == "rules (fallback)"
    assert "bad key" in plan.rationale[-1]
    assert Text(text="x").text == "x"
