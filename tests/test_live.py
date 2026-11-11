"""Live mode for the public demo (Session 9, ADR-037): caps, isolation, key handling."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from campaign_forge.config import DEFAULT_MODEL, Settings, isolated_settings
from campaign_forge.evaluation.offline import OfflineSearch, offline_responder
from campaign_forge.exceptions import (
    CapacityError,
    ConfigurationError,
    LiveQuotaError,
    WorkflowValidationError,
)
from campaign_forge.exports import run_json, run_markdown
from campaign_forge.logging_config import configure_logging
from campaign_forge.orchestration import (
    InMemoryRunStore,
    RunStatus,
    WorkflowEngine,
    default_registry,
    get_template,
)
from campaign_forge.providers import FakeProvider, ProviderRouter
from campaign_forge.providers.base import ModelClass
from campaign_forge.providers.openai_compat import OpenAICompatibleProvider
from campaign_forge.services import LiveLimits, LiveQuota, RunSlots
from campaign_forge.tools import ToolBox
from campaign_forge.ui import AppSession, Deployment, demo_session, live_session, visitor_key
from campaign_forge.ui import session as session_module
from campaign_forge.ui.views import EXAMPLE_BRIEF

from .sdk_http import http as httpx

VISITOR = "gsk_" + "A1b2C3d4" * 6
OWNER = "gsk_" + "Z9y8X7w6" * 6
EngineFactory = Callable[[Settings], WorkflowEngine]


def _limits(**overrides: Any) -> LiveLimits:
    values: dict[str, Any] = {
        "session_runs": 3,
        "daily_runs": 5,
        "concurrent_runs": 2,
        "run_calls": 25,
        "run_tokens": 30_000,
        "run_seconds": 1_200.0,
    }
    values.update(overrides)
    return LiveLimits(**values)


def _offline_engine(settings: Settings, gate: threading.Event | None = None) -> WorkflowEngine:
    """A live engine whose 'groq' provider is the offline responder (no network)."""
    router = ProviderRouter(settings, providers={"groq": FakeProvider(offline_responder)})
    engine = WorkflowEngine(
        default_registry(), router, store=InMemoryRunStore(), tools=ToolBox([OfflineSearch()])
    )
    if gate is not None:
        original = engine.start

        def gated(*args: Any, **kwargs: Any) -> Any:
            gate.wait(10)
            return original(*args, **kwargs)

        engine.start = gated  # type: ignore[method-assign]
    return engine


@pytest.fixture
def offline_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "live_engine", _offline_engine)


@pytest.fixture
def sessions() -> Iterator[list[AppSession]]:
    """Live sessions a test creates; every one is closed after its runs finish."""
    made: list[AppSession] = []
    yield made
    for session in made:
        assert session.service.wait(30)
        session.close()


def _live(
    sessions: list[AppSession], quota: LiveQuota, *, owner: bool = True, key: str = OWNER
) -> AppSession:
    session = live_session(key, key_source="owner" if owner else "visitor", quota=quota)
    sessions.append(session)
    return session


def _run(session: AppSession, template: str = "ad-pack") -> str:
    run_id = session.start(get_template(template), {"brief": EXAMPLE_BRIEF})
    assert session.service.wait(30)
    return run_id


# --------------------------------------------------------------------------- #
# Limits and accounting
# --------------------------------------------------------------------------- #
def test_settings_defaults_are_the_documented_caps() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert LiveLimits.from_settings(settings) == _limits()
    assert settings.live_owner_key is False and settings.live_visitor_keys is True


def test_cap_tightens_a_plan_and_never_loosens_it() -> None:
    limits = _limits()
    capped = limits.cap(get_template("full-campaign")).limits
    assert (capped.max_llm_calls, capped.max_total_tokens, capped.max_wall_seconds) == (
        25,
        30_000,
        1_200.0,
    )
    small = get_template("performance-review")  # 15 calls, 40,000 tokens, 1,200 s
    kept = limits.cap(small).limits
    assert (kept.max_llm_calls, kept.max_total_tokens) == (15, 30_000)
    assert "25 model calls, 30,000 tokens and 20 minutes" in limits.describe()


def test_daily_cap_with_reservations_refunds_and_a_new_day() -> None:
    day = [date(2026, 11, 11)]
    quota = LiveQuota(_limits(daily_runs=2), today=lambda: day[0])
    quota.reserve_owner_run()
    quota.reserve_owner_run()
    assert quota.owner_runs_left_today() == 0
    with pytest.raises(LiveQuotaError, match="2 live runs for today are used up"):
        quota.reserve_owner_run()
    quota.refund_owner_run()
    assert quota.owner_runs_today() == 1
    day[0] = date(2026, 11, 12)
    assert quota.owner_runs_today() == 0 and quota.owner_runs_left_today() == 2
    quota.refund_owner_run()  # never below zero
    assert quota.owner_runs_today() == 0


def test_run_slots() -> None:
    slots = RunSlots(2)
    slots.acquire()
    slots.acquire()
    with pytest.raises(CapacityError, match="the limit is 2"):
        slots.acquire()
    slots.release()
    assert slots.active == 1
    slots.release()
    slots.release()  # never below zero
    assert slots.active == 0
    with pytest.raises(ValueError):
        RunSlots(0)


# --------------------------------------------------------------------------- #
# Isolation and key handling
# --------------------------------------------------------------------------- #
def test_live_settings_never_reach_the_deployments_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-owner-openai-0123456789abcdef")
    monkeypatch.setenv("GEMINI_API_KEY", "owner-gemini-0123456789")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-owner-0123456789abcdef")
    monkeypatch.setenv("CF_FALLBACK_PROVIDERS", "gemini")
    monkeypatch.setenv("CF_AGENT_OVERRIDES", '{"copywriter": {"provider": "gemini"}}')
    monkeypatch.setenv("CF_PROVIDER_MODELS", '{"groq": "owner-model"}')
    monkeypatch.setenv("CF_RESEARCH_TOOLS", "tavily")
    monkeypatch.setenv("CF_UI_MODE", "local")
    live = session_module.live_settings(VISITOR)
    assert live.secret_values() == (VISITOR,)
    assert live.fallback_provider_names == () and live.agent_overrides == {}
    assert live.provider_models == {} and live.research_tool_names == ("wikipedia",)
    assert (live.provider, live.model, live.ui_mode) == ("groq", DEFAULT_MODEL, "demo")
    with pytest.raises(ConfigurationError, match="Invalid settings"):
        isolated_settings(max_tokens=0)


def test_live_settings_keep_a_groq_deployments_model_only() -> None:
    groq = Settings(_env_file=None, model="llama-next", temperature=0.2)  # type: ignore[call-arg]
    live = session_module.live_settings(VISITOR, groq)
    assert (live.model, live.temperature) == ("llama-next", 0.2)
    # Settings the deployment left at their defaults stay unset, so the model is not
    # pinned and cap-aware routing still sends long answers to the strong model.
    default = session_module.live_settings(VISITOR, Settings(_env_file=None))  # type: ignore[call-arg]
    assert "model" not in default.model_fields_set
    router = ProviderRouter(default)
    strong = router.profile("groq").model_for(ModelClass.STRONG)
    assert strong and strong != DEFAULT_MODEL
    assert router.resolve(model_class=ModelClass.STRONG, output_tokens=1).model == strong
    assert router.resolve(model_class=ModelClass.STANDARD, output_tokens=2_000).model == strong
    gemini = Settings(_env_file=None, provider="gemini", model="gemini-x")  # type: ignore[call-arg]
    assert session_module.live_settings(VISITOR, gemini).model == DEFAULT_MODEL


@pytest.mark.parametrize(
    "raw",
    ["", "sk-abcdefabcdefabcdefabcdef", "gsk_short", "gsk_" + "a" * 30 + " b", "gsk_" + "!" * 30],
)
def test_visitor_keys_are_checked_for_shape_without_echoing(raw: str) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        visitor_key(raw)
    assert raw.strip() == "" or raw not in str(excinfo.value)


def test_a_visitor_key_is_accepted_and_trimmed() -> None:
    assert visitor_key(f"  {VISITOR}\n") == VISITOR


def test_a_live_session_needs_a_quota_and_a_key_source() -> None:
    demo = demo_session()
    try:
        with pytest.raises(ConfigurationError):
            AppSession(mode="live", settings=demo.settings, engine=demo.engine, memory=demo.memory)
        assert demo.runs_left() is None
        definition = get_template("ad-pack")
        assert demo.capped(definition) is definition
    finally:
        demo.close()


# --------------------------------------------------------------------------- #
# Caps enforced on live sessions
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("offline_live")
def test_owner_runs_are_capped_per_session_and_per_day(sessions: list[AppSession]) -> None:
    quota = LiveQuota(_limits(session_runs=3, daily_runs=5))
    first = _live(sessions, quota)
    for expected_left in (2, 1, 0):
        _run(first)
        assert first.runs_left() == expected_left
    with pytest.raises(LiveQuotaError, match="used its 3 live runs"):
        first.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
    second = _live(sessions, quota)
    assert second.runs_left() == 2  # the day's cap is shared
    _run(second)
    _run(second)
    with pytest.raises(LiveQuotaError, match="5 live runs for today are used up"):
        second.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
    # A visitor's own key spends the visitor's quota: no session or daily cap.
    visitor = _live(sessions, quota, owner=False, key=VISITOR)
    assert visitor.runs_left() is None
    for _ in range(4):
        record = visitor.record(_run(visitor))
        assert record is not None and record.status is RunStatus.COMPLETED
    assert quota.owner_runs_today() == 5


@pytest.mark.usefixtures("offline_live")
def test_a_refused_start_is_not_counted(sessions: list[AppSession]) -> None:
    quota = LiveQuota(_limits())
    owner = _live(sessions, quota)
    template = get_template("ad-pack")
    steps = [s.model_copy(update={"agent": "no_such_agent"}) for s in template.steps]
    broken = template.model_copy(update={"steps": steps})
    with pytest.raises(WorkflowValidationError):
        owner.start(broken, {"brief": EXAMPLE_BRIEF})
    assert owner.live_runs == 0 and quota.owner_runs_today() == 0


@pytest.mark.usefixtures("offline_live")
def test_live_plans_are_capped_whatever_the_page_sends(sessions: list[AppSession]) -> None:
    owner = _live(sessions, LiveQuota(_limits(run_calls=25, run_tokens=30_000)))
    record = owner.record(_run(owner, "full-campaign"))
    assert record is not None
    assert record.definition.limits.max_llm_calls == 25
    assert record.definition.limits.max_total_tokens == 30_000
    assert record.status is RunStatus.COMPLETED  # an offline full campaign fits the caps


def test_live_runs_share_slots_across_sessions(
    sessions: list[AppSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    monkeypatch.setattr(session_module, "live_engine", lambda s: _offline_engine(s, gate))
    quota = LiveQuota(_limits(concurrent_runs=2))
    first, second, third = (_live(sessions, quota) for _ in range(3))
    try:
        first.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
        second.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
        assert quota.slots.active == 2
        with pytest.raises(CapacityError, match="the limit is 2"):
            third.start(get_template("ad-pack"), {"brief": EXAMPLE_BRIEF})
        assert third.live_runs == 0 and quota.owner_runs_today() == 2  # refunded
    finally:
        gate.set()
    assert first.service.wait(30) and second.service.wait(30)
    assert quota.slots.active == 0
    _run(third)
    assert quota.slots.active == 0 and quota.owner_runs_today() == 3


# --------------------------------------------------------------------------- #
# The deployment: what is offered
# --------------------------------------------------------------------------- #
def _deployment(**values: Any) -> Deployment:
    return Deployment.from_settings(Settings(_env_file=None, **values))  # type: ignore[call-arg]


def test_what_a_deployment_offers() -> None:
    plain = _deployment()
    assert plain.public and plain.visitor_keys_offered and not plain.owner_key_offered
    with pytest.raises(ConfigurationError, match="not offered"):
        plain.owner_session()
    keyed = _deployment(groq_api_key=OWNER)
    assert not keyed.owner_key_offered  # the owner has not opted in
    opted = _deployment(groq_api_key=OWNER, live_owner_key=True)
    assert opted.owner_key_offered
    assert not _deployment(live_owner_key=True).owner_key_offered  # no key configured
    local = _deployment(ui_mode="local", groq_api_key=OWNER, live_owner_key=True)
    assert not (local.public or local.owner_key_offered or local.visitor_keys_offered)
    assert not _deployment(provider="fake").public
    closed = _deployment(live_visitor_keys=False)
    with pytest.raises(ConfigurationError, match="not offered"):
        closed.visitor_session(VISITOR)
    with pytest.raises(ConfigurationError, match="Groq API key"):
        plain.visitor_session("not-a-key")
    first = plain.first_session()
    try:
        assert first.simulated
    finally:
        first.close()


@pytest.mark.usefixtures("offline_live")
def test_deployment_sessions_use_its_quota(sessions: list[AppSession]) -> None:
    deployment = _deployment(groq_api_key=OWNER, live_owner_key=True, live_daily_runs=7)
    owner = deployment.owner_session()
    visitor = deployment.visitor_session(VISITOR)
    sessions.extend([owner, visitor])
    assert owner.owner_key and owner.quota is deployment.quota
    assert owner.settings.secret("groq_api_key") == OWNER
    assert not visitor.owner_key and visitor.settings.secret("groq_api_key") == VISITOR
    assert owner.runs_left() == 3
    assert owner.memory.list_brands(owner.scope)  # the sample brand, as in demo mode


# --------------------------------------------------------------------------- #
# No key in logs, errors, run records or exports
# --------------------------------------------------------------------------- #
def _status_error(message: str) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return openai.AuthenticationError(message, response=response, body=None)


def _failing_engine(error: Exception) -> EngineFactory:
    """A live engine on the real OpenAI-compatible provider, whose HTTP client fails."""

    def create(**_: Any) -> Any:
        raise error

    def build(settings: Settings) -> WorkflowEngine:
        def factory(profile: Any, route: Any) -> OpenAICompatibleProvider:
            completions = SimpleNamespace(create=create)
            client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
            return OpenAICompatibleProvider(
                profile,
                model=route.model,
                api_key=settings.secret("groq_api_key"),
                max_retries=0,
                client=client,  # type: ignore[arg-type]
            )

        router = ProviderRouter(settings, factory=factory)
        return WorkflowEngine(
            default_registry(), router, store=InMemoryRunStore(), tools=ToolBox([OfflineSearch()])
        )

    return build


@pytest.mark.parametrize(
    "error",
    [
        _status_error(f"Invalid API Key: {VISITOR}"),
        RuntimeError(f"connection reset while sending {VISITOR}"),
    ],
    ids=["provider-echoes-the-key", "unexpected-error-with-the-key"],
)
def test_no_key_reaches_logs_errors_records_or_exports(
    error: Exception,
    sessions: list[AppSession],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")
    monkeypatch.setattr(session_module, "live_engine", _failing_engine(error))
    visitor = _live(sessions, LiveQuota(_limits()), owner=False, key=VISITOR)
    run_id = _run(visitor)
    record = visitor.record(run_id)
    surfaces = [
        visitor.error(run_id) or "",
        capsys.readouterr().err,
        repr(visitor),
        repr(visitor.settings),
        json.dumps([d.text for d in visitor.memory.list_documents(visitor.scope)]),
    ]
    if record is not None:
        assert record.status is not RunStatus.COMPLETED
        surfaces += [record.model_dump_json(), run_json(record), run_markdown(record)]
    assert any("[REDACTED]" in s for s in surfaces), "the error was reported, redacted"
    for surface in surfaces:
        assert VISITOR not in surface
