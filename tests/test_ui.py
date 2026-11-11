"""Session 6: run exports, reasoning stripping, and the app's view models and session.

None of these tests need Streamlit: the app's logic lives in plain modules.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from campaign_forge.config import Settings
from campaign_forge.core.budget import BudgetLimits
from campaign_forge.domain import Artifact, ProducedBy
from campaign_forge.evaluation.offline import offline_responder
from campaign_forge.exceptions import CampaignForgeError, WorkflowValidationError
from campaign_forge.exports import (
    SIMULATED_NOTICE,
    artifact_markdown,
    is_simulated,
    markdown_to_html,
    run_html,
    run_json,
    run_markdown,
)
from campaign_forge.orchestration import (
    InMemoryRunStore,
    RunStatus,
    WorkflowEngine,
    default_registry,
    get_template,
)
from campaign_forge.parsing import strip_reasoning
from campaign_forge.providers import FakeProvider, ProviderRouter
from campaign_forge.ui import (
    SAMPLE_BRAND,
    AppSession,
    build_session,
    demo_session,
    local_session,
    views,
)
from campaign_forge.ui.views import EXAMPLE_BRIEF

from .orchestration_helpers import make_agent, make_engine, step, topic, workflow


@pytest.fixture
def demo() -> Any:
    session = demo_session()
    yield session
    session.close()


def _run(session: AppSession, template: str = "full-campaign", **inputs: Any) -> Any:
    run_id = session.start(get_template(template), {"brief": EXAMPLE_BRIEF, **inputs})
    record = session.record(run_id)
    assert record is not None
    return record


# --------------------------------------------------------------------------- #
# Reasoning never surfaces
# --------------------------------------------------------------------------- #
def test_strip_reasoning_removes_inline_chain_of_thought() -> None:
    assert strip_reasoning('<think>plan it\nstep 2</think>\n{"a": 1}') == '{"a": 1}'
    assert strip_reasoning("<THINKING>x</THINKING>Answer <reasoning>y</reasoning>done") == (
        "Answer done"
    )
    assert strip_reasoning("<think>cut off while reasoning") == ""  # nothing usable left
    assert strip_reasoning("no reasoning here") == "no reasoning here"


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def test_exports_of_a_simulated_full_campaign(demo: AppSession) -> None:
    record = _run(demo)
    assert record.status is RunStatus.COMPLETED and is_simulated(record)
    markdown = run_markdown(record)
    assert markdown.startswith(f"> {SIMULATED_NOTICE}")
    assert "# Campaign Brief - EcoSip Reusable Bottle" in markdown
    assert f"Run: {record.run_id}" in markdown and "Status: completed" in markdown

    payload = json.loads(run_json(record))
    assert payload["simulated"] is True and payload["run"]["run_id"] == record.run_id
    assert payload["run"]["status"] == "completed"

    page = run_html(record)
    assert page.startswith("<!doctype html>") and '<p class="notice">SIMULATED' in page
    assert "<h1>Campaign Brief - EcoSip Reusable Bottle</h1>" in page
    assert "<script" not in page and "http" not in page.split("<body>")[0]  # no external assets


def test_html_export_never_passes_raw_html_or_script_links() -> None:
    hostile = "# T\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1)) <b>bold</b>"
    rendered = markdown_to_html(hostile)
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert 'href="javascript' not in rendered and "<b>" not in rendered
    assert "<table>" in markdown_to_html("| a | b |\n| - | - |\n| 1 | 2 |")


def test_markdown_export_falls_back_for_runs_without_a_report(demo: AppSession) -> None:
    classic = _run(demo, "classic-campaign")
    assert "# Campaign Brief" in run_markdown(classic)  # the v1 final brief
    kit = _run(demo, "brand-kit")
    markdown = run_markdown(kit)
    assert "## Brand" in markdown and "## Brand Check" in markdown
    record = kit.model_copy(update={"events": []})  # no simulated provider events
    assert not is_simulated(record) and "SIMULATED" not in run_html(record)


def test_artifact_markdown_handles_every_shape() -> None:
    report = Artifact(
        kind="report.campaign_brief@1",
        data={"title": "T", "markdown": "# Report body", "missing_inputs": [], "assumptions": []},
        produced_by=ProducedBy(agent_id="t"),
    )
    assert artifact_markdown(report) == "# Report body"  # the report itself, not its digest
    plan = Artifact(
        kind="research.plan@1", data={"queries": ["q"]}, produced_by=ProducedBy(agent_id="t")
    )
    assert artifact_markdown(plan) == "- q"

    def art(data: dict[str, Any], kind: str = "custom.thing@1") -> Artifact:
        return Artifact(kind=kind, data=data, produced_by=ProducedBy(agent_id="t"))

    assert artifact_markdown(art({"text": "hello"})) == "hello"
    assert artifact_markdown(art({"markdown": "# Hi"})) == "# Hi"
    assert artifact_markdown(art({"prompts": ["a", "b"]})) == "- a\n- b"
    assert artifact_markdown(art({"a": 1, "b": 2})).startswith("```json")
    by = ProducedBy(agent_id="t")
    brand = Artifact.from_model("brand.profile@1", SAMPLE_BRAND, produced_by=by)
    assert "**Banned terms:** revolutionary" in artifact_markdown(brand)


def test_runs_without_a_report_or_outputs_export_cleanly() -> None:
    engine, _ = make_engine(make_agent("a"))
    bare = engine.start(workflow(step("a", "a")), topic()).record
    assert "_This run produced no outputs._" in run_markdown(bare)
    flow = workflow(step("a", "a"), outputs={"out": "step:a"})
    assert "## Out" in run_markdown(engine.start(flow, topic()).record)


# --------------------------------------------------------------------------- #
# View models
# --------------------------------------------------------------------------- #
def test_dag_dot_shows_inputs_steps_conditions_and_status(demo: AppSession) -> None:
    definition = get_template("full-campaign")
    planned = views.dag_dot(definition)
    assert planned.startswith("digraph workflow {") and planned.rstrip().endswith("}")
    assert '"input:brand" [label="brand (optional)"' in planned
    assert '"step:email" [label="email\\nemail_marketing\\n(optional)\\n[planned]"' in planned
    assert '"input:brief" -> "step:audience" [color="#cfd8dc"];' in planned
    assert '"step:copy" -> "step:ads";' in planned
    skipped = views.dag_dot(definition.with_disabled({"visuals"}))
    assert "[disabled]" in skipped and views.STATUS_COLORS["disabled"] in skipped

    record = _run(demo)
    done = views.dag_dot(record.definition, record.steps)
    assert "[succeeded]" in done and "[skipped]" in done  # brand_check skipped without a brand
    gated = workflow(step("a", "x", when=({"source": "input:topic", "op": "exists"},)))
    assert '[style=dashed, label="if"]' in views.dag_dot(gated)


def test_table_rows(demo: AppSession) -> None:
    record = _run(demo, "brand-kit")
    steps = views.step_rows(record)
    assert [r["step"] for r in steps] == ["audience", "brand", "positioning", "copy", "brand_check"]
    assert all(r["status"] == "succeeded" and r["seconds"] is not None for r in steps)
    assert steps[3]["review"] is not None  # the copy step is reviewed
    runs = views.run_rows([record])
    assert runs[0]["workflow"] == "brand-kit" and runs[0]["calls"] == record.llm_calls
    usage = views.usage_summary(record)
    assert usage["steps finished"] == "5/5" and usage["model calls"] == record.llm_calls

    plan = views.plan_rows(get_template("full-campaign"))
    by_step = {r["step"]: r for r in plan}
    assert by_step["copy"]["reviewed"].startswith("by critic")
    assert by_step["email"]["condition"] == "input:brief.channels contains 'email'"
    assert by_step["brand_check"]["condition"] == "input:brand exists"

    assert views.provenance(record.outputs()["brand"])["provider"] == "fake"
    research = record.resolve("step:audience")  # cites the simulated search's evidence
    assert research is not None
    assert views.claim_rows(research) and views.evidence_rows(research)
    agents = views.agent_rows(demo.engine.registry)
    assert {"brand_strategist", "brand_consistency", "legacy.manager"} <= {a["id"] for a in agents}


def test_customize_skips_optional_steps_and_sets_limits() -> None:
    definition = get_template("full-campaign")
    limits = definition.limits.model_copy(update={"max_llm_calls": 12})
    custom = views.customize(definition, skip=["visuals", "social"], limits=limits)
    assert {s.id for s in custom.steps if not s.enabled} == {"visuals", "social"}
    assert custom.limits.max_llm_calls == 12
    assert views.customize(definition) == definition
    with pytest.raises(CampaignForgeError, match="Only optional steps"):
        views.customize(definition, skip=["copy"])
    assert isinstance(BudgetLimits.model_validate(custom.limits), BudgetLimits)


def test_studios_map_onto_real_templates() -> None:
    assert len(views.STUDIOS) == 7
    for studio in views.STUDIOS:
        assert studio.planned is None  # every studio has templates since Session 8
        assert views.template_ids(studio) == list(studio.templates)
    assert "full-campaign" in views.template_ids()


def test_input_parsers() -> None:
    raw = EXAMPLE_BRIEF.model_dump_json()
    assert views.parse_brief_json(raw) == EXAMPLE_BRIEF
    assert views.parse_brief_json(f"[{raw}]") == EXAMPLE_BRIEF
    with pytest.raises(CampaignForgeError, match="exactly one"):
        views.parse_brief_json(f"[{raw}, {raw}]")
    with pytest.raises(CampaignForgeError, match="Not valid JSON"):
        views.parse_brief_json(b"{nope")
    with pytest.raises(ValidationError) as caught:
        views.parse_brief_json('{"product_name": "X"}')
    assert any(e.startswith("product_description:") for e in views.errors_of(caught.value))

    assert views.parse_brand_json(SAMPLE_BRAND.model_dump_json()) == SAMPLE_BRAND
    with pytest.raises(CampaignForgeError, match="Not valid JSON"):
        views.parse_brand_json("nope")

    bundle = views.text_sources([("notes", "  Hiking clubs  "), ("empty.md", "   ")])
    assert bundle is not None and [d.name for d in bundle.documents] == ["notes"]
    assert views.text_sources([("x", "")]) is None
    assert views.split_terms("a, b\n c,,") == ["a", "b", "c"]


def test_health_rows_never_show_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "gsk_" + "S" * 30
    settings = Settings(_env_file=None, groq_api_key=secret, research_tools="")  # type: ignore[call-arg]
    rows = views.health_rows(settings, ProviderRouter(settings), mode="local")
    text = json.dumps(rows)
    assert secret not in text
    values = {r["setting"]: r["value"] for r in rows}
    assert values["GROQ_API_KEY"] == "set" and values["OPENAI_API_KEY"] == "not set"
    assert values["Model (strong)"] == "groq / openai/gpt-oss-120b"
    assert values["Research tools"] == "none" and values["Run store"].startswith("JSON")
    sqlite = settings.model_copy(update={"run_store": "sqlite"})
    assert "SQLite" in json.dumps(views.health_rows(sqlite, ProviderRouter(sqlite), mode="local"))

    paid = Settings(_env_file=None, provider="openai")  # type: ignore[call-arg]
    demo_rows = views.health_rows(paid, ProviderRouter(paid), mode="demo")  # FREE_ONLY refuses
    assert any(r["value"].startswith("not available") for r in demo_rows)
    assert any("nothing on the server" in r["value"] for r in demo_rows)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def test_demo_session_is_simulated_in_memory_and_seeded(demo: AppSession) -> None:
    assert demo.simulated and not demo.background
    assert [b.name for b in demo.memory.list_brands(demo.scope)] == ["EcoSip"]
    record = _run(demo, "full-campaign", brand=SAMPLE_BRAND)
    assert record.steps["brand_check"].status.value == "succeeded"
    assert demo.runs()[0].run_id == record.run_id
    history = demo.memory.list_documents(demo.scope, kind="campaign")
    assert [d.ref for d in history] == [record.run_id]  # completed runs are remembered
    assert demo.record("run-unknown") is None and not demo.is_running(record.run_id)
    with pytest.raises(WorkflowValidationError):
        demo.start(get_template("full-campaign"), {})  # nothing created on invalid input
    assert len(demo.runs()) == 1


def test_build_session_picks_the_mode(tmp_path: Path) -> None:
    demo = build_session(Settings(_env_file=None))  # type: ignore[call-arg]
    assert demo.mode == "demo"
    demo.close()
    fake = build_session(Settings(_env_file=None, provider="fake", ui_mode="local"))  # type: ignore[call-arg]
    assert fake.mode == "demo"  # CF_PROVIDER=fake is always simulated
    fake.close()
    local = build_session(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            ui_mode="local",
            groq_api_key="gsk_" + "k" * 30,
            memory_db=tmp_path / "memory.db",
            runs_dir=tmp_path / "runs",
        )
    )
    assert local.mode == "local" and local.background
    local.close()


def test_local_session_closes_its_memory_when_the_engine_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import campaign_forge.cli_runs as cli_runs

    def broken(settings: Settings) -> WorkflowEngine:
        raise CampaignForgeError("no engine")

    monkeypatch.setattr(cli_runs, "build_engine", broken)
    settings = Settings(_env_file=None, ui_mode="local", memory_db=tmp_path / "m.db")  # type: ignore[call-arg]
    with pytest.raises(CampaignForgeError, match="no engine"):
        local_session(settings)  # the autouse leak check proves the memory was closed


def _background_session(
    engine: WorkflowEngine, tmp_path: Path, *, mode: str = "local", history: bool = True
) -> AppSession:
    from campaign_forge.memory import SqliteMemoryStore

    settings = Settings(_env_file=None, provider="fake", memory_history=history)  # type: ignore[call-arg]
    return AppSession(
        mode=mode,  # type: ignore[arg-type]
        settings=settings,
        engine=engine,
        memory=SqliteMemoryStore(tmp_path / "m.db"),
    )


def _wait(session: AppSession, run_id: str) -> None:
    assert session.service.wait(30)
    assert not session.is_running(run_id)


def test_background_runs_report_progress_and_errors(tmp_path: Path) -> None:
    router = ProviderRouter(
        Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        providers={"fake": FakeProvider(offline_responder)},
    )
    engine = WorkflowEngine(default_registry(), router, store=InMemoryRunStore())
    session = _background_session(engine, tmp_path)
    try:
        run_id = session.start(get_template("brand-kit"), {"brief": EXAMPLE_BRIEF})
        _wait(session, run_id)
        record = session.record(run_id)
        assert record is not None and record.status is RunStatus.COMPLETED
        assert session.error(run_id) is None
        assert session.memory.list_documents(session.scope)  # remembered from the thread

        gate = threading.Event()

        def stuck(*args: Any, **kwargs: Any) -> Any:
            gate.wait(5)
            raise CampaignForgeError("provider exploded")

        engine.start = stuck  # type: ignore[method-assign]
        failing = session.start(get_template("brand-kit"), {"brief": EXAMPLE_BRIEF})
        assert session.is_running(failing)
        gate.set()
        _wait(session, failing)
        assert session.error(failing) == "provider exploded" and session.record(failing) is None

        def crash(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("bug")

        engine.start = crash  # type: ignore[method-assign]
        crashed = session.start(get_template("brand-kit"), {"brief": EXAMPLE_BRIEF})
        _wait(session, crashed)
        assert session.error(crashed) == "Unexpected RuntimeError: bug"
    finally:
        session.close()


def test_session_actions_approve_reject_cancel_and_resume(tmp_path: Path) -> None:
    from campaign_forge.exceptions import RateLimitedError

    engine, _ = make_engine(make_agent("a"), make_agent("b"))
    session = _background_session(engine, tmp_path, mode="demo")  # actions run synchronously
    try:
        gated = workflow(step("a", "a"), step("b", "b", after=("a",), approval=True))
        run_id = session.start(gated, topic())
        record = session.record(run_id)
        assert record is not None and record.status is RunStatus.WAITING_FOR_APPROVAL
        session.approve(run_id, "b", approved=True, comment="fine")
        approved = session.record(run_id)
        assert approved is not None and approved.status is RunStatus.COMPLETED

        rejected_id = session.start(gated, topic())
        session.approve(rejected_id, "b", approved=False)
        rejected = session.record(rejected_id)
        assert rejected is not None and rejected.status is RunStatus.CANCELLED

        waiting_id = session.start(gated, topic())
        assert session.cancel(waiting_id).status is RunStatus.CANCELLED
    finally:
        session.close()

    flaky = FakeProvider("ok", failures=[RateLimitedError("slow", retry_after=1.0)])
    engine, _ = make_engine(make_agent("a"), provider=flaky)
    session = _background_session(engine, tmp_path / "second", mode="demo", history=False)
    try:
        run_id = session.start(workflow(step("a", "a")), topic())
        paused = session.record(run_id)
        assert paused is not None and paused.status is RunStatus.PAUSED
        session.resume(run_id)
        resumed = session.record(run_id)
        assert resumed is not None and resumed.status is RunStatus.COMPLETED
        assert session.memory.list_documents(session.scope) == []  # history turned off
    finally:
        session.close()
