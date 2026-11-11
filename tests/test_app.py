"""The Streamlit app, driven headlessly with ``streamlit.testing.v1.AppTest``.

Skipped when Streamlit (the ``[ui]`` extra) is not installed; CI installs it.
Every test runs in demo mode unless it injects its own session.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from campaign_forge.config import Settings  # noqa: E402
from campaign_forge.evaluation.offline import OfflineSearch, offline_responder  # noqa: E402
from campaign_forge.exceptions import RateLimitedError  # noqa: E402
from campaign_forge.memory import SqliteMemoryStore  # noqa: E402
from campaign_forge.orchestration import (  # noqa: E402
    InMemoryRunStore,
    RunStatus,
    WorkflowEngine,
    default_registry,
    get_template,
)
from campaign_forge.providers import FakeProvider, ProviderRouter  # noqa: E402
from campaign_forge.services import LiveLimits, LiveQuota  # noqa: E402
from campaign_forge.tools import ToolBox  # noqa: E402
from campaign_forge.ui import AppSession, demo_session, live_session  # noqa: E402
from campaign_forge.ui import session as session_module  # noqa: E402
from campaign_forge.ui.pages import (  # noqa: E402
    CUSTOM_DEFINITION,
    DEMO_SESSION_KEY,
    SELECTED_RUN,
    SESSION_KEY,
    deployment,
)
from campaign_forge.ui.views import EXAMPLE_BRIEF  # noqa: E402

from .orchestration_helpers import make_agent, make_engine, step, topic, workflow  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
PAGES = sorted(p.name for p in (APP.parent / "views").glob("*.py"))


OWNER_KEY = "gsk_" + "Owner123" * 6
VISITOR_KEY = "gsk_" + "Visit123" * 6


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[AppTest]:
    monkeypatch.setenv("CF_UI_MODE", "demo")
    deployment.clear()  # shared by every session of a process: rebuilt from this test's env
    at = AppTest.from_file(str(APP), default_timeout=60)
    yield at
    deployment.clear()
    for key in (SESSION_KEY, DEMO_SESSION_KEY):
        try:
            session = at.session_state[key]
        except KeyError:
            continue
        assert session.service.wait(30)
        session.close()


def _offline_live_engine(settings: Settings) -> WorkflowEngine:
    router = ProviderRouter(settings, providers={"groq": FakeProvider(offline_responder)})
    return WorkflowEngine(
        default_registry(), router, store=InMemoryRunStore(), tools=ToolBox([OfflineSearch()])
    )


@pytest.fixture
def offline_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live sessions answer with the offline responder: no network, no key used."""
    monkeypatch.setattr(session_module, "live_engine", _offline_live_engine)


def _ok(at: AppTest) -> AppTest:
    assert not at.exception, [e.value for e in at.exception]
    return at


def _button(at: AppTest, label: str) -> Any:
    return next(b for b in at.button if b.label == label)


def _follow(at: AppTest, page: str) -> AppTest:
    """Make AppTest's current page the one ``st.switch_page`` showed.

    A browser follows ``st.switch_page``; AppTest keeps rerunning the page it was
    last pointed at, so interactions after a switch must re-select the page.
    """
    assert at.title[0].value, "the switched-to page rendered"
    return at.switch_page(f"views/{page}")


def _session(at: AppTest) -> AppSession:
    session = at.session_state[SESSION_KEY]
    assert isinstance(session, AppSession)
    return session


def _texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [w.value for w in at.warning]
    parts += [i.value for i in at.info] + [s.value for s in at.success]
    parts += [e.value for e in at.error]
    return "\n".join(str(p) for p in parts)


# --------------------------------------------------------------------------- #
# Every page renders with no keys
# --------------------------------------------------------------------------- #
def test_the_app_has_all_sixteen_pages() -> None:
    assert len(PAGES) == 16


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_in_demo_mode(app: AppTest, page: str) -> None:
    _ok(app.run())
    _ok(app.switch_page(f"views/{page}").run())
    assert app.title, page
    assert "Demo mode" in "\n".join(w.value for w in app.sidebar.warning)


def test_every_studio_opens_its_own_templates(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/studio_seo.py").run())
    assert app.selectbox(key="seo-template").options == ["SEO content pack (seo-content-pack)"]
    _ok(app.switch_page("views/studio_analytics.py").run())
    assert app.selectbox(key="analytics-template").value == "performance-review"
    _ok(app.switch_page("views/studio_research.py").run())
    assert app.selectbox(key="research-template").options == [
        "Competitor report (competitor-report)",
        "Full campaign (agent library) (full-campaign)",
    ]


# --------------------------------------------------------------------------- #
# New Run -> Run Inspector
# --------------------------------------------------------------------------- #
def test_a_demo_run_from_plan_to_downloads(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/new_run.py").run())
    assert app.get("graphviz_chart") and app.dataframe  # the plan preview
    app.multiselect(key="new-skip").set_value(["visuals"])
    app.selectbox(key="new-brand").set_value("EcoSip")
    app.text_area(key="new-notes").set_value("Hiking clubs respond to trail photos.")
    _ok(app.run())
    _ok(app.button(key="new-run").click().run())
    assert app.title[0].value == "Run Inspector"
    text = _texts(app)
    assert "SIMULATED" in text and "completed" in text
    record = _session(app).runs()[0]
    assert record.status is RunStatus.COMPLETED
    assert record.steps["visuals"].status.value == "skipped"
    assert record.steps["brand_check"].status.value == "succeeded"
    assert "sources" in record.inputs and "brand" in record.inputs
    assert {b.label for b in app.get("download_button")} == {"Markdown", "JSON", "HTML"}

    _ok(app.switch_page("views/dashboard.py").run())
    assert [m.value for m in app.metric][:2] == ["1", "1"]


def test_an_invalid_brief_cannot_be_run(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/new_run.py").run())
    app.text_input(key="new-name").set_value("")
    _ok(app.run())
    assert "product_name" in app.error[0].value
    assert app.button(key="new-run").disabled
    assert _session(app).runs() == []


def test_brand_kit_run_can_save_its_brand(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/new_run.py").run())
    app.selectbox(key="new-template").set_value("brand-kit")
    _ok(app.run())
    _ok(app.button(key="new-run").click().run())
    _follow(app, "inspector.py")
    _ok(_button(app, "Save this brand to Brand Memory").click().run())
    assert "Saved brand EcoSip Reusable Bottle" in app.success[0].value
    names = [b.name for b in _session(app).memory.list_brands(_session(app).scope)]
    assert "EcoSip Reusable Bottle" in names


def test_approval_gates_from_a_custom_workflow(app: AppTest) -> None:
    definition = get_template("ad-pack")
    steps = tuple(s.model_copy(update={"approval": s.id == "ads"}) for s in definition.steps)
    app.session_state[CUSTOM_DEFINITION] = definition.model_copy(
        update={"steps": steps}
    ).model_dump_json()
    _ok(app.run())
    _ok(app.switch_page("views/new_run.py").run())
    assert app.selectbox(key="new-template").value == "custom"
    _ok(app.button(key="new-run").click().run())
    assert "waiting for your approval" in _texts(app)
    _follow(app, "inspector.py")
    app.text_input(key=f"comment-{_session(app).runs()[0].run_id}-ads").set_value("Looks good")
    _ok(_button(app, "Approve").click().run())
    assert _session(app).runs()[0].status is RunStatus.COMPLETED


def test_a_paused_run_resumes_from_its_checkpoint(app: AppTest, tmp_path: Path) -> None:
    flaky = FakeProvider("ok", failures=[RateLimitedError("slow down", retry_after=30.0)])
    engine, _ = make_engine(make_agent("a"), make_agent("b"), provider=flaky)
    session = AppSession(
        mode="demo",
        settings=Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        engine=engine,
        memory=SqliteMemoryStore(),
    )
    run_id = session.start(workflow(step("a", "a"), step("b", "b", after=("a",))), topic())
    app.session_state[SESSION_KEY] = session
    app.session_state[SELECTED_RUN] = run_id
    _ok(app.run())
    _ok(app.switch_page("views/inspector.py").run())
    assert "paused" in _texts(app) and "about 30 s" in app.caption[0].value
    _ok(_button(app, "Resume from checkpoint").click().run())
    assert session.record(run_id).status is RunStatus.COMPLETED  # type: ignore[union-attr]

    second = session.start(workflow(step("a", "a")), topic())  # completes immediately
    app.session_state[SELECTED_RUN] = second
    _ok(app.run())
    assert not [b for b in app.button if b.label == "Cancel run"]


def test_cancel_a_waiting_run(app: AppTest, tmp_path: Path) -> None:
    engine, _ = make_engine(make_agent("a"), make_agent("b"))
    session = AppSession(
        mode="demo",
        settings=Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        engine=engine,
        memory=SqliteMemoryStore(),
    )
    gated = workflow(step("a", "a"), step("b", "b", after=("a",), approval=True))
    run_id = session.start(gated, topic())
    app.session_state[SESSION_KEY] = session
    app.session_state[SELECTED_RUN] = run_id
    _ok(app.run())
    _ok(app.switch_page("views/inspector.py").run())
    _ok(_button(app, "Cancel run").click().run())
    assert session.record(run_id).status is RunStatus.CANCELLED  # type: ignore[union-attr]


def test_background_runs_show_live_progress(app: AppTest, tmp_path: Path) -> None:
    router = ProviderRouter(
        Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        providers={"fake": FakeProvider(offline_responder)},
    )
    engine = WorkflowEngine(default_registry(), router, store=InMemoryRunStore())
    gate = threading.Event()
    original = engine.start

    def slow_start(*args: Any, **kwargs: Any) -> Any:
        gate.wait(10)
        return original(*args, **kwargs)

    engine.start = slow_start  # type: ignore[method-assign]
    session = AppSession(
        mode="local",
        settings=Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        engine=engine,
        memory=SqliteMemoryStore(tmp_path / "memory.db"),
    )
    run_id = session.start(get_template("brand-kit"), {"brief": EXAMPLE_BRIEF})
    app.session_state[SESSION_KEY] = session
    app.session_state[SELECTED_RUN] = run_id
    _ok(app.run())
    assert "Local mode" in app.sidebar.info[0].value
    _ok(app.switch_page("views/inspector.py").run())
    assert "Running" in app.info[0].value
    gate.set()
    assert session.service.wait(30)
    _ok(app.run())
    assert "completed" in _texts(app)


# --------------------------------------------------------------------------- #
# Library pages
# --------------------------------------------------------------------------- #
def test_brand_memory_create_and_delete(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/brand_memory.py").run())
    assert "EcoSip" in app.selectbox(key="brand-selected").options  # the demo sample
    inputs = {t.label: t for t in app.text_input}
    inputs["Name"].set_value("Northwind")
    inputs["Banned terms (comma-separated)"].set_value("cheap, hype")
    next(t for t in app.text_area if t.label.startswith("Voice")).set_value("Calm and exact.")
    _ok(_button(app, "Save brand").click().run())
    assert "Saved brand Northwind" in app.success[0].value
    store = _session(app).memory
    stored = store.get_brand(_session(app).scope, "Northwind")
    assert stored.banned_terms == ["cheap", "hype"]

    {t.label: t for t in app.text_input}["Name"].set_value("")  # the form keeps its values
    _ok(_button(app, "Save brand").click().run())
    assert "not valid" in app.error[0].value

    app.selectbox(key="brand-selected").set_value("Northwind")
    _ok(app.run())
    _ok(_button(app, "Delete brand").click().run())
    assert [b.name for b in store.list_brands(_session(app).scope)] == ["EcoSip"]


def test_history_lists_runs_campaigns_and_notes(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/history.py").run())
    assert "No runs yet." in _texts(app)
    session = _session(app)
    run_id = session.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
    _ok(app.run())
    assert app.expander  # the remembered campaign
    app.text_input(key="history-query").set_value("EcoSip")
    _ok(app.run())
    assert any("EcoSip" in e.label for e in app.expander)
    app.text_area(key="history-note").set_value("Try trail photos in week two")
    _ok(app.run())
    _ok(app.button(key="history-save-note").click().run())
    assert session.memory.list_documents(session.scope, kind="note")
    note = session.memory.list_documents(session.scope, kind="note")[0]
    _ok(app.button(key=f"delete-note-{note.id}").click().run())
    assert session.memory.list_documents(session.scope, kind="note") == []
    _ok(app.button(key="history-open").click().run())
    assert app.title[0].value == "Run Inspector"
    assert app.selectbox(key="inspector-run").value == run_id


def test_workflow_builder_validates_and_hands_over_to_new_run(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/workflow_builder.py").run())
    assert "Valid: full-campaign" in app.success[0].value
    app.multiselect(key="builder-skip").set_value(["social", "visuals"])
    _ok(app.run())
    _ok(app.button(key="builder-use").click().run())
    assert app.title[0].value == "New Run"
    assert app.selectbox(key="new-template").value == "custom"
    _follow(app, "new_run.py")
    _ok(app.run())
    _ok(app.button(key="new-run").click().run())
    record = _session(app).runs()[0]
    assert {s for s, st in record.steps.items() if st.status.value == "skipped"} >= {
        "social",
        "visuals",
    }


def test_settings_and_health_show_no_secrets(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/settings.py").run())
    _ok(_button(app, "Check the provider").click().run())
    assert app.success[0].value.startswith("fake: OK")


def test_inspector_without_runs_points_to_new_run(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/inspector.py").run())
    assert "No runs yet" in app.info[0].value


def test_a_fresh_demo_session_is_isolated() -> None:
    first, second = demo_session(), demo_session()
    try:
        first.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
        assert len(first.runs()) == 1 and second.runs() == []
    finally:
        first.close()
        second.close()


def test_evaluation_lab_runs_a_simulated_benchmark(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/evaluation_lab.py").run())
    app.selectbox(key="lab-dataset").set_value("injection")
    _ok(app.run())
    assert len(app.multiselect(key="lab-cases-injection-False").value) == 14
    _ok(app.button(key="lab-run").click().run())
    assert "SIMULATED benchmark" in app.warning[0].value
    assert [m.value for m in app.metric][0] == "14/14"
    assert "No regressions against the baseline" in app.success[0].value
    assert {b.label for b in app.get("download_button")} == {"Report (JSON)", "Report (Markdown)"}
    app.multiselect(key="lab-cases-injection-False").set_value(["direct-goal"])
    _ok(app.button(key="lab-run").click().run())
    assert "run them all to compare" in "\n".join(c.value for c in app.caption)
    app.selectbox(key="lab-workflow").set_value("ad-pack")
    _ok(app.button(key="lab-run").click().run())
    assert "No bundled baseline" in "\n".join(c.value for c in app.caption)


# --------------------------------------------------------------------------- #
# Live mode (Session 9, ADR-037)
# --------------------------------------------------------------------------- #
def _shown(at: AppTest) -> str:
    """Everything the page shows as text, sidebar included.

    Text-field values are left out: AppTest keeps a submitted value, while the browser
    clears a ``clear_on_submit`` form itself.
    """
    parts = [_texts(at)] + [c.value for c in at.caption] + [s.value for s in at.subheader]
    parts += [e.value for e in at.sidebar.success] + [e.value for e in at.sidebar.warning]
    return "\n".join(str(p) for p in parts)


@pytest.mark.usefixtures("offline_live")
def test_live_mode_on_the_demo_key_is_capped(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", OWNER_KEY)
    monkeypatch.setenv("CF_LIVE_OWNER_KEY", "true")
    monkeypatch.setenv("CF_LIVE_SESSION_RUNS", "1")
    _ok(app.run())
    demo = _session(app)
    _ok(app.switch_page("views/settings.py").run())
    assert "1 runs per visit, 5 of 5 left today" in _shown(app)
    _ok(_button(app, "Go live on the demo's key").click().run())
    live = _session(app)
    assert live.mode == "live" and live.owner_key
    assert "Live mode** on the demo's key" in _shown(app) and "Live runs left: 1" in _shown(app)

    _ok(app.switch_page("views/new_run.py").run())
    assert app.number_input(key="new-live-calls").value == 25  # full-campaign allows 40
    assert app.number_input(key="new-live-tokens").value == 30_000
    assert "Live runs left on the demo's key: 1" in _shown(app)
    _ok(_button(app, "Approve and run").click().run())
    assert live.service.wait(30) and live.live_runs == 1
    assert live.runs()[0].definition.limits.max_llm_calls == 25

    _ok(app.switch_page("views/new_run.py").run())
    _ok(_button(app, "Approve and run").click().run())
    assert "used its 1 live runs" in app.error[0].value
    assert len(live.runs()) == 1

    _ok(app.switch_page("views/settings.py").run())
    assert OWNER_KEY not in _shown(app)
    _ok(_button(app, "Back to demo mode").click().run())
    assert _session(app) is demo  # the demo session's runs and brands are kept


@pytest.mark.usefixtures("offline_live")
def test_live_mode_with_a_visitor_key(app: AppTest) -> None:
    _ok(app.run())
    _ok(app.switch_page("views/settings.py").run())
    assert "Go live on the demo's key" not in {b.label for b in app.button}  # not opted in
    field = next(t for t in app.text_input if t.label == "Your Groq API key")
    field.set_value("not-a-key")
    _ok(_button(app, "Go live with my key").click().run())
    assert "does not look like a Groq API key" in app.error[0].value
    assert _session(app).simulated and "not-a-key" not in _shown(app)

    field = next(t for t in app.text_input if t.label == "Your Groq API key")
    field.set_value(VISITOR_KEY)
    _ok(_button(app, "Go live with my key").click().run())
    live = _session(app)
    assert live.mode == "live" and not live.owner_key and live.runs_left() is None
    assert live.settings.secret("groq_api_key") == VISITOR_KEY
    assert "your own Groq key" in _shown(app)
    assert VISITOR_KEY not in _shown(app)
    assert not [f for f in app.text_input if f.label == "Your Groq API key"]  # form gone

    _ok(app.switch_page("views/new_run.py").run())
    _ok(_button(app, "Approve and run").click().run())
    assert live.service.wait(30)
    assert live.runs()[0].status is RunStatus.COMPLETED

    _ok(app.switch_page("views/settings.py").run())
    _ok(_button(app, "Forget my key").click().run())
    assert _session(app).simulated
    with pytest.raises(KeyError):
        app.session_state[DEMO_SESSION_KEY]


def test_live_mode_is_not_offered_when_switched_off(
    app: AppTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CF_LIVE_VISITOR_KEYS", "false")
    monkeypatch.delenv("CF_LIVE_OWNER_KEY", raising=False)
    _ok(app.run())
    assert "Try live mode" not in str(app.main)
    _ok(app.switch_page("views/settings.py").run())
    assert "Live mode" not in [s.value for s in app.subheader]


@pytest.mark.usefixtures("offline_live")
def test_the_lab_stays_simulated_in_live_mode(app: AppTest) -> None:
    quota = LiveQuota(LiveLimits.from_settings(Settings(_env_file=None)))  # type: ignore[call-arg]
    app.session_state[SESSION_KEY] = live_session(VISITOR_KEY, key_source="visitor", quota=quota)
    _ok(app.run())
    _ok(app.switch_page("views/evaluation_lab.py").run())
    assert not app.toggle  # live benchmarks only in local mode
    assert "Benchmarks are simulated in the public app" in _shown(app)
    _ok(app.switch_page("views/dashboard.py").run())
    assert "Live mode on your own Groq key" in _shown(app)
