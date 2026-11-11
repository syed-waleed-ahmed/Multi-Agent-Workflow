"""Tests for the 2.0 CLI commands (agents, workflows, execute, runs) with a scripted provider."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from campaign_forge import cli, cli_runs
from campaign_forge.config import Settings
from campaign_forge.exceptions import QuotaExhaustedError
from campaign_forge.orchestration import (
    JsonFileRunStore,
    WorkflowEngine,
    classic_campaign,
    default_registry,
)
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter

from .conftest import ART_PAYLOAD, COPY_PAYLOAD, MANAGER_TEXT, RESEARCH_TEXT

SCRIPT = {
    "legacy.research": RESEARCH_TEXT,
    "legacy.copywriter": json.dumps(COPY_PAYLOAD),
    "legacy.art_director": json.dumps(ART_PAYLOAD),
    "legacy.manager": MANAGER_TEXT,
}
BRIEF = {
    "product_name": "EcoSip",
    "product_description": "Insulated bottle.",
    "target_audience": "hikers",
    "goal": "sales",
    "tone": "fun",
    "channels": ["email"],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the CLI engine factory: scripted fake provider, runs stored under tmp_path."""
    state: dict[str, Any] = {"quota": False, "store": tmp_path / "runs"}

    def responder(request: CompletionRequest) -> str:
        agent = request.metadata["agent"]
        if state["quota"] and agent == "legacy.copywriter":
            raise QuotaExhaustedError("tokens per day", resume_after=60.0)
        return SCRIPT[agent]

    def factory(settings: Settings) -> WorkflowEngine:
        router = ProviderRouter(
            Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
            providers={"fake": FakeProvider(responder)},
        )
        return WorkflowEngine(default_registry(), router, store=JsonFileRunStore(state["store"]))

    monkeypatch.setattr(cli_runs, "build_engine", factory)
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps(BRIEF), encoding="utf-8")
    state["brief"] = brief
    return state


def _json(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


def test_agents_and_workflows_listing(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["agents", "list", "--json"]) == 0
    assert {a["id"] for a in _json(capsys)} >= {"legacy.research", "legacy.manager"}
    assert cli.main(["agents", "list"]) == 0
    assert "legacy.copywriter" in capsys.readouterr().out
    assert cli.main(["workflows", "list", "--json"]) == 0
    ids = {w["id"] for w in _json(capsys)}
    assert {"full-campaign", "classic-campaign", "ad-pack"} <= ids
    assert cli.main(["workflows", "list"]) == 0
    assert "classic-campaign" in capsys.readouterr().out
    assert cli.main(["workflows", "show", "classic-campaign"]) == 0
    assert _json(capsys)["steps"][0]["id"] == "research"


def test_workflow_validation_from_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.json"
    good.write_text(classic_campaign().model_dump_json(), encoding="utf-8")
    assert cli.main(["workflows", "validate", str(good)]) == 0
    assert "Valid" in capsys.readouterr().out
    bad_data = json.loads(classic_campaign().model_dump_json())
    bad_data["steps"][0]["agent"] = "ghost"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_data), encoding="utf-8")
    assert cli.main(["workflows", "validate", str(bad)]) == 2
    assert "unknown agent" in capsys.readouterr().out


def test_execute_then_inspect(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["execute", "classic-campaign", "--brief", str(env["brief"])]) == 0
    out = capsys.readouterr().out
    assert "Final brief" in out and "completed" in out
    assert cli.main(["runs", "list", "--json"]) == 0
    [summary] = _json(capsys)
    assert summary["status"] == "completed" and summary["llm_calls"] == 4
    assert cli.main(["runs", "show", summary["run_id"], "--json"]) == 0
    shown = _json(capsys)
    assert shown["outputs"]["final_brief"]["text"] == MANAGER_TEXT
    assert cli.main(["runs", "list"]) == 0
    assert summary["run_id"] in capsys.readouterr().out
    assert cli.main(["runs", "show", "run-missing"]) == 1


def test_execute_requires_exactly_one_brief(env: dict[str, Any], tmp_path: Path) -> None:
    many = tmp_path / "many.json"
    many.write_text(json.dumps([BRIEF, BRIEF]), encoding="utf-8")
    assert cli.main(["execute", "classic-campaign", "--brief", str(many)]) == 2


def test_pause_resume_and_cancel(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    env["quota"] = True
    assert cli.main(["execute", "classic-campaign", "--brief", str(env["brief"])]) == 3
    out = capsys.readouterr().out
    assert "Resume with" in out and "60s" in out
    assert cli.main(["runs", "list", "--json"]) == 0
    run_id = _json(capsys)[0]["run_id"]
    env["quota"] = False
    assert cli.main(["runs", "resume", run_id, "--json"]) == 0
    assert _json(capsys)["status"] == "completed"

    env["quota"] = True
    assert cli.main(["execute", "classic-campaign", "--brief", str(env["brief"])]) == 3
    capsys.readouterr()
    assert cli.main(["runs", "list", "--json"]) == 0
    paused = next(r for r in _json(capsys) if r["status"] == "paused")
    assert cli.main(["runs", "cancel", paused["run_id"]]) == 0
    assert "cancelled" in capsys.readouterr().out


def test_approval_flow(
    env: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(classic_campaign().model_dump_json())
    data["steps"][1]["approval"] = True  # pause after the copy step
    flow = tmp_path / "approval.json"
    flow.write_text(json.dumps(data), encoding="utf-8")
    assert cli.main(["execute", str(flow), "--brief", str(env["brief"])]) == 3
    assert "Approve with" in capsys.readouterr().out
    assert cli.main(["runs", "list", "--json"]) == 0
    run_id = _json(capsys)[0]["run_id"]
    assert cli.main(["runs", "approve", run_id, "copy", "--comment", "ok"]) == 0
    capsys.readouterr()

    assert cli.main(["execute", str(flow), "--brief", str(env["brief"])]) == 3
    capsys.readouterr()
    assert cli.main(["runs", "list", "--json"]) == 0
    waiting = next(r for r in _json(capsys) if r["status"] == "waiting_for_approval")
    assert cli.main(["runs", "approve", waiting["run_id"], "copy", "--reject"]) == 1


def test_fake_provider_mode_runs_the_full_campaign_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CF_PROVIDER=fake gives a key-free, clearly labeled simulated run (no patched factory)."""
    monkeypatch.setenv("CF_PROVIDER", "fake")
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    from campaign_forge.config import get_settings

    get_settings.cache_clear()
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps(BRIEF), encoding="utf-8")
    try:
        code = cli.main(["execute", "full-campaign", "--brief", str(brief)])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert code == 0
    assert "SIMULATED" in out and "Final brief" in out


def test_d21_a_console_that_cannot_encode_the_brief_does_not_crash(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Found by the Session 10 live smoke test: Windows, stdout redirected to a file
    # (cp1252), and a model answer with a non-breaking hyphen (U+2011).
    hyphen = chr(0x2011)  # built in code: the source stays ASCII
    monkeypatch.setitem(SCRIPT, "legacy.manager", f"{MANAGER_TEXT}\n\nTrail{hyphen}ready.")
    raw = io.BytesIO()
    narrow = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", narrow)
    assert cli.main(["execute", "classic-campaign", "--brief", str(env["brief"])]) == 0
    narrow.flush()
    assert b"Trail?ready" in raw.getvalue()


def test_the_planner_chooses_the_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CF_PROVIDER", "fake")  # the offline responder answers the planner
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps(BRIEF), encoding="utf-8")

    assert cli.main(["plan", "--brief", str(brief), "--json"]) == 0
    plan = _json(capsys)
    assert (plan["workflow"], plan["planner"]) == ("full-campaign", "llm")
    assert "report" in plan["steps"] and plan["skipped"] == []
    assert cli.main(["plan", "--brief", str(brief), "--rules", "--json"]) == 0
    assert _json(capsys)["planner"] == "rules"
    assert cli.main(["plan", "--brief", str(brief)]) == 0
    assert "execute auto" in capsys.readouterr().out

    assert cli.main(["execute", "auto", "--brief", str(brief)]) == 0
    out = capsys.readouterr().out
    assert "Plan (llm): full-campaign" in out and "Status: completed" in out
    assert cli.main(["execute", "auto", "--brief", str(brief), "--json"]) == 0
    assert _json(capsys)["status"] == "completed"
