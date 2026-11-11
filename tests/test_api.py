"""The HTTP API, driven with FastAPI's TestClient. Skipped without the ``[api]`` extra."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from campaign_forge.api import create_app  # noqa: E402
from campaign_forge.api.__main__ import main as api_main  # noqa: E402
from campaign_forge.api.app import _scope_for  # noqa: E402
from campaign_forge.config import Settings  # noqa: E402
from campaign_forge.evaluation.offline import offline_responder  # noqa: E402
from campaign_forge.exceptions import RateLimitedError  # noqa: E402
from campaign_forge.memory import SqliteMemoryStore  # noqa: E402
from campaign_forge.orchestration import (  # noqa: E402
    InMemoryRunStore,
    WorkflowEngine,
    default_registry,
    get_template,
)
from campaign_forge.providers import FakeProvider, ProviderRouter  # noqa: E402
from campaign_forge.services import Platform  # noqa: E402
from campaign_forge.ui.views import EXAMPLE_BRIEF  # noqa: E402

ALPHA = "key-alpha-0123456789"
BETA = "key-beta-0123456789"
A = {"X-API-Key": ALPHA}
B = {"X-API-Key": BETA}
BRIEF = json.loads(EXAMPLE_BRIEF.model_dump_json())
OPENAPI = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider": "fake",
        "memory_db": tmp_path / "memory.db",
        "runs_dir": tmp_path / "runs",
        "api_keys": f"{ALPHA},{BETA}",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def platform(tmp_path: Path) -> Iterator[Platform]:
    built = Platform(_settings(tmp_path))
    yield built
    built.close()


def _client(platform: Platform, **kwargs: Any) -> TestClient:
    return TestClient(create_app(platform.settings, platform=platform), **kwargs)


@pytest.fixture
def client(platform: Platform) -> Iterator[TestClient]:
    with _client(platform) as test_client:
        yield test_client


def _done(platform: Platform) -> None:
    assert platform.runs.wait(30), "background runs did not finish"


def _error(response: Any, status: int, kind: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["type"] == kind
    return body  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Health, authentication, tenancy
# --------------------------------------------------------------------------- #
def test_health_is_open_and_everything_else_needs_a_key(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    _error(client.get("/api/v1/agents"), 401, "unauthorized")
    wrong = client.get("/api/v1/agents", headers={"X-API-Key": "nope"})
    assert _error(wrong, 401, "unauthorized")["message"].startswith("Missing or invalid")
    assert wrong.headers["WWW-Authenticate"] == "X-API-Key"
    assert client.get("/api/v1/agents", headers=A).status_code == 200
    _error(client.get("/api/v1/nowhere", headers=A), 404, "not_found")


def test_without_configured_keys_the_api_is_closed(tmp_path: Path) -> None:
    closed = Platform(_settings(tmp_path, api_keys=None))
    try:
        with _client(closed) as client:
            body = _error(client.get("/api/v1/agents", headers=A), 401, "unauthorized")
            assert "CF_API_KEYS" in body["message"]
            assert client.get("/health").status_code == 200
    finally:
        closed.close()


def test_each_key_is_its_own_tenant(client: TestClient, platform: Platform) -> None:
    started = client.post(
        "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=A
    )
    assert started.status_code == 202
    run_id = started.json()["id"]
    _done(platform)
    assert client.get(f"/api/v1/campaigns/{run_id}", headers=A).status_code == 200
    _error(client.get(f"/api/v1/campaigns/{run_id}", headers=B), 404, "not_found")
    _error(client.post(f"/api/v1/executions/{run_id}/cancel", headers=B), 404, "not_found")
    assert [e["id"] for e in client.get("/api/v1/executions", headers=A).json()] == [run_id]
    assert client.get("/api/v1/executions", headers=B).json() == []


# --------------------------------------------------------------------------- #
# Agents, providers, status
# --------------------------------------------------------------------------- #
def test_agents_providers_and_status(client: TestClient) -> None:
    agents = client.get("/api/v1/agents", headers=A).json()
    assert {"copywriter", "brand_consistency", "legacy.manager"} <= {a["id"] for a in agents}
    detail = client.get("/api/v1/agents/copywriter", headers=A).json()
    assert detail["output_kind"] == "content.core_copy@1" and detail["limits"]["max_tokens"]
    assert any(i["name"] == "brand" and not i["required"] for i in detail["inputs"])
    _error(client.get("/api/v1/agents/ghost", headers=A), 404, "not_found")

    providers = {p["name"]: p for p in client.get("/api/v1/providers", headers=A).json()}
    assert providers["fake"]["is_global"] and not providers["groq"]["configured"]
    assert providers["openai"]["allowed"] is False  # FREE_ONLY refuses paid providers
    status = client.get("/api/v1/system/status", headers=A).json()
    assert status["simulated"] is True and status["keys_configured"] == 2
    assert status["auth"] == "api_key" and status["active_runs"] == 0
    assert status["research_tools"] == ["offline_search"]  # what actually runs when simulated
    for body in (agents, providers, status):
        assert ALPHA not in json.dumps(body) and BETA not in json.dumps(body)


# --------------------------------------------------------------------------- #
# Campaigns and executions
# --------------------------------------------------------------------------- #
def test_a_campaign_runs_in_the_background_to_a_report(
    client: TestClient, platform: Platform
) -> None:
    started = client.post("/api/v1/campaigns", json={"brief": BRIEF}, headers=A)
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.headers["Location"] == f"/api/v1/campaigns/{run_id}"
    _done(platform)
    campaign = client.get(f"/api/v1/campaigns/{run_id}", headers=A).json()
    assert campaign["status"] == "completed" and campaign["simulated"] is True
    assert campaign["workflow"] == "full-campaign@1.0.0" and not campaign["running"]
    assert campaign["report_markdown"].startswith("# Campaign Brief - EcoSip")
    assert campaign["brief"]["product_name"] == EXAMPLE_BRIEF.product_name
    assert campaign["usage"]["llm_calls"] == 14 and len(campaign["steps"]) == 11
    assert campaign["outputs"]["copy"]["data"]["tagline"]
    assert campaign["links"]["self"] == f"/api/v1/executions/{run_id}"
    execution = client.get(f"/api/v1/executions/{run_id}", headers=A).json()
    assert execution["status"] == "completed" and "report_markdown" not in execution
    assert execution["outputs"]["report"]["markdown"] == campaign["report_markdown"]
    _error(client.post(f"/api/v1/executions/{run_id}/cancel", headers=A), 409, "conflict")
    _error(client.get("/api/v1/executions/run-unknown", headers=A), 404, "not_found")


def test_campaign_options_brand_sources_skips_and_tighter_limits(
    client: TestClient, platform: Platform
) -> None:
    request = {
        "brief": BRIEF,
        "brand": {"name": "EcoSip", "voice": "Warm.", "banned_terms": ["cheap"]},
        "sources": [{"name": "notes", "content": "Hiking clubs love trail photos."}],
        "skip_steps": ["visuals", "visuals", "social"],
        "max_llm_calls": 500,  # above the template's 40: ignored, never raised
        "max_total_tokens": 90_000,
    }
    run_id = client.post("/api/v1/campaigns", json=request, headers=A).json()["id"]
    _done(platform)
    record = platform.execution(_scope_for(ALPHA), run_id).record
    assert record is not None and record.status.value == "completed"
    assert {s for s, st in record.steps.items() if st.status.value == "skipped"} == {
        "visuals",
        "social",
    }
    assert record.steps["brand_check"].status.value == "succeeded"
    assert record.limits.max_llm_calls == 40 and record.limits.max_total_tokens == 90_000
    assert set(record.inputs) == {"brief", "brand", "sources"}


def test_invalid_requests_are_rejected_before_any_run(
    client: TestClient, platform: Platform
) -> None:
    bad_brief = client.post("/api/v1/campaigns", json={"brief": {"product_name": "X"}}, headers=A)
    body = _error(bad_brief, 422, "invalid_request")
    assert any("product_description" in d for d in body["details"])
    _error(
        client.post("/api/v1/campaigns", json={"brief": BRIEF, "nope": 1}, headers=A),
        422,
        "invalid_request",
    )
    unknown = client.post(
        "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ghost"}, headers=A
    )
    assert "full-campaign" in _error(unknown, 404, "not_found")["message"]
    skip_required = {"brief": BRIEF, "skip_steps": ["copy"]}
    _error(client.post("/api/v1/campaigns", json=skip_required, headers=A), 400, "bad_request")
    branded_v1 = {
        "brief": BRIEF,
        "workflow": "classic-campaign",
        "brand": {"name": "X", "voice": "Y"},
    }
    assert (
        "does not take: brand"
        in _error(client.post("/api/v1/campaigns", json=branded_v1, headers=A), 400, "bad_request")[
            "message"
        ]
    )
    assert client.get("/api/v1/executions", headers=A).json() == []


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #
def _custom(workflow_id: str = "lean-ads", **step_updates: Any) -> dict[str, Any]:
    definition = get_template("ad-pack")
    steps = [s.model_copy(update=step_updates.get(s.id, {})) for s in definition.steps]
    custom = definition.model_copy(
        update={"id": workflow_id, "name": "Lean ads", "steps": tuple(steps)}
    )
    return json.loads(custom.model_dump_json())


def test_custom_workflows_are_validated_scoped_and_runnable(
    client: TestClient, platform: Platform
) -> None:
    listed = client.get("/api/v1/workflows", headers=A).json()
    assert {"full-campaign", "brand-kit"} <= {w["id"] for w in listed}
    assert all(w["source"] == "builtin" for w in listed)
    created = client.post("/api/v1/workflows", json=_custom(), headers=A)
    assert (
        created.status_code == 201 and created.headers["Location"] == "/api/v1/workflows/lean-ads"
    )
    assert created.json()["source"] == "custom" and created.json()["steps"] == 4
    assert (
        client.get("/api/v1/workflows/lean-ads", headers=A).json()["definition"]["id"] == "lean-ads"
    )
    assert client.get("/api/v1/workflows/full-campaign", headers=A).json()["source"] == "builtin"
    _error(client.get("/api/v1/workflows/lean-ads", headers=B), 404, "not_found")
    _error(client.post("/api/v1/workflows", json=_custom("ad-pack"), headers=A), 400, "bad_request")
    broken = _custom("broken")
    broken["steps"][0]["agent"] = "ghost_agent"
    assert any(
        "ghost_agent" in d
        for d in _error(
            client.post("/api/v1/workflows", json=broken, headers=A), 422, "invalid_workflow"
        )["details"]
    )
    _error(client.post("/api/v1/workflows", json={"id": "x"}, headers=A), 422, "invalid_request")

    run = client.post("/api/v1/workflows/lean-ads/run", json={"brief": BRIEF}, headers=A)
    assert run.status_code == 202 and run.headers["Location"].startswith("/api/v1/executions/")
    _done(platform)
    done = client.get(run.headers["Location"], headers=A).json()
    assert done["status"] == "completed" and done["workflow"] == "lean-ads@1.0.0"
    _error(
        client.post("/api/v1/workflows/lean-ads/run", json={"brief": BRIEF}, headers=B),
        404,
        "not_found",
    )


def test_approval_gates_over_http(client: TestClient, platform: Platform) -> None:
    gated = _custom("gated-ads", ads={"approval": True})
    assert client.post("/api/v1/workflows", json=gated, headers=A).status_code == 201
    approved_id = client.post(
        "/api/v1/workflows/gated-ads/run", json={"brief": BRIEF}, headers=A
    ).json()["id"]
    rejected_id = client.post(
        "/api/v1/workflows/gated-ads/run", json={"brief": BRIEF}, headers=A
    ).json()["id"]
    _done(platform)
    waiting = client.get(f"/api/v1/executions/{approved_id}", headers=A).json()
    assert waiting["status"] == "waiting_for_approval"
    ok = client.post(
        f"/api/v1/executions/{approved_id}/approve",
        json={"step_id": "ads", "comment": "Ship it"},
        headers=A,
    )
    assert ok.status_code == 202
    client.post(
        f"/api/v1/executions/{rejected_id}/approve",
        json={"step_id": "ads", "approved": False},
        headers=A,
    )
    _done(platform)
    assert (
        client.get(f"/api/v1/executions/{approved_id}", headers=A).json()["status"] == "completed"
    )
    assert (
        client.get(f"/api/v1/executions/{rejected_id}", headers=A).json()["status"] == "cancelled"
    )


def test_paused_runs_resume_from_their_checkpoint(tmp_path: Path) -> None:
    fake = FakeProvider(offline_responder, failures=[RateLimitedError("slow", retry_after=5.0)])
    settings = _settings(tmp_path)
    router = ProviderRouter(settings, providers={"fake": fake})
    engine = WorkflowEngine(default_registry(), router, store=InMemoryRunStore())
    memory = SqliteMemoryStore()
    flaky = Platform(settings, engine=engine, memory=memory)
    try:
        with _client(flaky) as client:
            run_id = client.post(
                "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=A
            ).json()["id"]
            _done(flaky)
            paused = client.get(f"/api/v1/executions/{run_id}", headers=A).json()
            assert paused["status"] == "paused" and paused["resume_after"] == 5.0
            assert client.post(f"/api/v1/executions/{run_id}/resume", headers=A).status_code == 202
            _done(flaky)
            assert (
                client.get(f"/api/v1/executions/{run_id}", headers=A).json()["status"]
                == "completed"
            )
            _error(client.post("/api/v1/executions/run-ghost/resume", headers=A), 404, "not_found")
    finally:
        memory.close()


# --------------------------------------------------------------------------- #
# Limits and failures
# --------------------------------------------------------------------------- #
def test_request_and_run_limits_per_key(tmp_path: Path) -> None:
    limited = Platform(_settings(tmp_path, api_rate_limit=3, api_run_limit=1))
    try:
        with _client(limited) as client:
            for _ in range(3):
                assert client.get("/api/v1/agents", headers=A).status_code == 200
            blocked = client.get("/api/v1/agents", headers=A)
            _error(blocked, 429, "rate_limited")
            assert int(blocked.headers["Retry-After"]) >= 1
            assert client.get("/api/v1/agents", headers=B).status_code == 200  # own bucket
            first = client.post(
                "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=B
            )
            assert first.status_code == 202
            again = client.post(
                "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=B
            )
            assert "run starts" in _error(again, 429, "rate_limited")["message"]
            _done(limited)
    finally:
        limited.close()


def test_capacity_and_runs_that_are_still_starting(tmp_path: Path) -> None:
    capped = Platform(_settings(tmp_path, api_max_active_runs=1))
    gate = threading.Event()
    original = capped.engine.start

    def slow_start(*args: Any, **kwargs: Any) -> Any:
        gate.wait(10)
        return original(*args, **kwargs)

    capped.engine.start = slow_start  # type: ignore[method-assign]
    try:
        with _client(capped) as client:
            first = client.post(
                "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=A
            )
            assert first.status_code == 202 and first.json()["status"] == "starting"
            run_id = first.json()["id"]
            starting = client.get(f"/api/v1/executions/{run_id}", headers=A).json()
            assert starting["status"] == "starting" and starting["running"] is True
            _error(client.get(f"/api/v1/executions/{run_id}", headers=B), 404, "not_found")
            busy = client.post(
                "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=A
            )
            _error(busy, 503, "at_capacity")
            assert busy.headers["Retry-After"] == "30"
            assert client.get("/api/v1/system/status", headers=A).json()["active_runs"] == 1
            gate.set()
            _done(capped)
            assert (
                client.get(f"/api/v1/executions/{run_id}", headers=A).json()["status"]
                == "completed"
            )
    finally:
        gate.set()
        capped.close()


def test_background_errors_and_unexpected_failures_are_safe(
    client: TestClient, platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"boom with {ALPHA}")

    monkeypatch.setattr(platform.engine, "start", explode)
    run_id = client.post(
        "/api/v1/campaigns", json={"brief": BRIEF, "workflow": "ad-pack"}, headers=A
    ).json()["id"]
    _done(platform)
    failed = client.get(f"/api/v1/executions/{run_id}", headers=A).json()
    assert failed["status"] == "failed" and failed["running"] is False
    assert failed["error"].startswith("Unexpected RuntimeError")

    monkeypatch.setattr(platform, "agents", explode)
    with _client(platform, raise_server_exceptions=False) as raw:
        crashed = raw.get("/api/v1/agents", headers=A)
    body = _error(crashed, 500, "internal_error")
    assert body["message"] == "Internal server error." and ALPHA not in crashed.text


def test_error_messages_are_redacted(client: TestClient) -> None:
    leak = client.post("/api/v1/campaigns", json={"brief": BRIEF, "workflow": ALPHA}, headers=A)
    body = _error(leak, 404, "not_found")
    assert ALPHA not in leak.text and "[REDACTED]" in body["message"]


# --------------------------------------------------------------------------- #
# OpenAPI and entry point
# --------------------------------------------------------------------------- #
def test_openapi_schema_is_published_and_current(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])
    assert {
        "/health",
        "/api/v1/agents",
        "/api/v1/agents/{agent_id}",
        "/api/v1/campaigns",
        "/api/v1/campaigns/{campaign_id}",
        "/api/v1/workflows",
        "/api/v1/workflows/{workflow_id}",
        "/api/v1/workflows/{workflow_id}/run",
        "/api/v1/executions/{execution_id}",
        "/api/v1/executions/{execution_id}/resume",
        "/api/v1/executions/{execution_id}/cancel",
        "/api/v1/providers",
        "/api/v1/system/status",
    } <= paths
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    published = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert published == schema, "docs/openapi.json is stale: run python scripts/export_openapi.py"


def test_module_entry_point_serves_with_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    api_main(["--port", "9001"])
    assert calls == [
        (
            ("campaign_forge.api.app:create_app",),
            {"factory": True, "host": "127.0.0.1", "port": 9001},
        )
    ]
