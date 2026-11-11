"""The Evaluation Lab: datasets, scoring, regressions, the bench CLI and the app page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from campaign_forge import cli
from campaign_forge.config import Settings
from campaign_forge.evaluation.offline import OfflineSearch, offline_responder
from campaign_forge.exceptions import ConfigurationError, RateLimitedError
from campaign_forge.orchestration import WorkflowEngine, default_registry, get_template
from campaign_forge.orchestration.factory import simulated_engine
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter
from campaign_forge.services.lab import (
    BUNDLED_DATASETS,
    LabCase,
    bundled_baseline,
    compare,
    load_dataset,
    load_report,
    run_benchmark,
    run_case,
)
from campaign_forge.tools import ToolBox

from .orchestration_helpers import step, workflow

BASELINES = Path(__file__).resolve().parents[1] / "src/campaign_forge/evaluation/baselines"


def _engine(responder: Any = offline_responder, **fake: Any) -> WorkflowEngine:
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    router = ProviderRouter(settings, providers={"fake": FakeProvider(responder, **fake)})
    return WorkflowEngine(default_registry(), router, tools=ToolBox([OfflineSearch()]))


def _case(**brief: Any) -> LabCase:
    values = {
        "product_name": "EcoSip Bottle",
        "product_description": "Insulated bottle.",
        "target_audience": "hikers",
        "goal": "sales",
        "tone": "fresh",
        "channels": ["instagram"],
    }
    values.update(brief)
    return LabCase(id="case-1", brief=values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Datasets
# --------------------------------------------------------------------------- #
def test_bundled_datasets_and_files(tmp_path: Path) -> None:
    corpus_name, corpus = load_dataset("corpus")
    injection_name, injection = load_dataset("injection")
    assert (corpus_name, len(corpus)) == ("corpus", 10)
    assert (injection_name, len(injection)) == ("injection", 14)
    assert {c.category for c in injection} == {"injection"}
    assert any(c.sources for c in injection)  # indirect injection through user sources
    assert set(BUNDLED_DATASETS) == {"corpus", "injection"}
    custom = tmp_path / "mine.json"
    custom.write_text(json.dumps([{"id": "x", "brief": corpus[0].brief.model_dump()}]))
    assert load_dataset(custom)[0] == "mine"
    custom.write_text('[{"id": "x"}]')
    with pytest.raises(ConfigurationError, match="not valid"):
        load_dataset(custom)
    with pytest.raises(ConfigurationError, match="Bundled datasets: corpus, injection"):
        load_dataset("nope")


# --------------------------------------------------------------------------- #
# Running and scoring
# --------------------------------------------------------------------------- #
def test_simulated_benchmark_of_the_injection_dataset() -> None:
    _, cases = load_dataset("injection")
    report = run_benchmark(
        simulated_engine(), get_template("full-campaign"), cases, dataset="injection", version="t"
    )
    s = report.summary
    assert (s.passed, s.cases, s.success_rate, s.failure_rate) == (14, 14, 1.0, 0.0)
    assert report.mode == "simulated" and report.provider == "fake"
    assert report.models == {
        "small": "fake-model",
        "standard": "fake-model",
        "strong": "fake-model",
    }
    assert s.avg_calls > 10 and s.total_tokens > 0 and s.estimated_cost_usd == 0.0
    assert s.avg_review_score == 0.75
    markdown = report.to_markdown()
    assert "## full-campaign@1.0.0 on injection (simulated)" in markdown
    assert "| indirect-source | injection | pass |" in markdown


def test_cases_get_the_inputs_their_workflow_takes() -> None:
    engine = _engine()
    repurpose = get_template("content-repurpose")
    without_sources = run_case(engine, repurpose, _case())
    assert without_sources.passed  # the brief's description becomes the text to repurpose
    brand = {"name": "EcoSip", "voice": "Warm.", "banned_terms": ["cheap"]}
    branded = LabCase(id="b", brief=_case().brief, brand=brand)  # type: ignore[arg-type]
    result = run_case(engine, get_template("full-campaign"), branded)
    assert result.passed and any(c.name.startswith("brand_check") for c in result.checks)
    assert run_case(engine, get_template("classic-campaign"), branded).passed  # brand ignored


def test_failures_errors_and_retries_are_recorded() -> None:
    echoing = run_case(_engine(), get_template("ad-pack"), _case(product_name="Banana Bottle"))
    assert not echoing.passed and "no_injection_echo" in echoing.problem()

    invalid = workflow(step("a", "ghost_agent"))
    errored = run_case(_engine(), invalid, _case())
    assert (
        errored.status == "error"
        and errored.error
        and errored.error.startswith("WorkflowValidationError")
    )
    assert errored.problem() == errored.error

    def no_sleep(*_: Any) -> None:
        return None

    flaky = _engine(failures=[RateLimitedError("slow", retry_after=0.01)])
    import campaign_forge.orchestration.engine as engine_module

    original = engine_module.time.sleep
    engine_module.time.sleep = no_sleep  # type: ignore[assignment]
    try:
        retried = run_case(flaky, get_template("ad-pack"), _case())
    finally:
        engine_module.time.sleep = original  # type: ignore[assignment]
    assert retried.passed and retried.retries >= 1


def test_regressions_against_a_baseline() -> None:
    _, cases = load_dataset("corpus")
    engine = simulated_engine()
    definition = get_template("ad-pack")
    baseline = run_benchmark(engine, definition, cases[:3], dataset="corpus", version="t")
    assert compare(baseline, baseline) == []
    worse = baseline.model_copy(deep=True)
    worse.summary.success_rate = 0.5
    worse.summary.avg_calls = baseline.summary.avg_calls * 1.5
    worse.summary.avg_tokens = baseline.summary.avg_tokens * 1.5
    worse.results[0].passed = False
    problems = compare(worse, baseline)
    assert len(problems) == 4 and problems[-1] == f"newly failing: {baseline.results[0].id}"
    other = baseline.model_copy(update={"dataset": "injection"})
    with pytest.raises(ConfigurationError, match="baseline is for"):
        compare(other, baseline)


def test_bundled_baselines_load_and_match_a_fresh_run(tmp_path: Path) -> None:
    baseline = bundled_baseline("full-campaign", "corpus")
    assert baseline is not None and baseline.summary.cases == 10
    assert bundled_baseline("full-campaign", "nope") is None
    _, cases = load_dataset("corpus")
    fresh = run_benchmark(
        simulated_engine(), get_template("full-campaign"), cases, dataset="corpus", version="t"
    )
    assert compare(fresh, baseline) == []
    broken = tmp_path / "bad.json"
    broken.write_text("{}")
    with pytest.raises(ConfigurationError, match="Cannot read benchmark report"):
        load_report(broken)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_bench_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "out" / "report.json"
    markdown = tmp_path / "out" / "report.md"
    saved = tmp_path / "out" / "baseline.json"
    code = cli.main(
        [
            "bench",
            "ad-pack",
            "--offline",
            "--only",
            "baseline-ecommerce",
            "minimal-input",
            "--report",
            str(report),
            "--markdown",
            str(markdown),
            "--save-baseline",
            str(saved),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "2/2 passed" in out and "provider fake" in out
    assert json.loads(report.read_text())["summary"]["cases"] == 2
    assert markdown.read_text().startswith("## ad-pack@1.0.0 on corpus (simulated)")

    again = cli.main(
        [
            "bench",
            "ad-pack",
            "--offline",
            "--only",
            "baseline-ecommerce",
            "minimal-input",
            "--baseline",
            str(saved),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert again == 0 and payload["regressions"] == [] and payload["mode"] == "simulated"

    full = str(BASELINES / "full-campaign.corpus.json")
    assert cli.main(["bench", "ad-pack", "--offline", "--baseline", full]) == 2
    assert "baseline is for" in capsys.readouterr().out
    assert cli.main(["bench", "ad-pack", "--offline", "--only", "ghost"]) == 2

    failing = tmp_path / "bad-cases.json"
    _, cases = load_dataset("corpus")
    bad = cases[0].model_dump(mode="json")
    bad["brief"]["product_name"] = "Banana Bottle"  # the offline answer echoes a canary
    failing.write_text(json.dumps([bad]))
    assert cli.main(["bench", "ad-pack", "--offline", "--dataset", str(failing)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_bench_cli_accepts_a_workflow_file(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "flow.json"
    path.write_text(get_template("email-sequence").model_dump_json())
    assert cli.main(["bench", str(path), "--offline", "--only", "baseline-ecommerce"]) == 0
    assert "email-sequence" in capsys.readouterr().out


def test_offline_answers_cover_every_registered_agent() -> None:
    for spec in default_registry().specs():
        request = CompletionRequest.from_prompts("s", "u", metadata={"agent": spec.id})
        assert offline_responder(request)


def test_an_unexpected_crash_is_recorded_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = simulated_engine()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(engine, "start", explode)
    result = run_case(engine, get_template("ad-pack"), _case())
    assert result.status == "error" and result.error == (
        "PermissionError: [WinError 5] Access is denied"
    )


def test_reports_name_the_models_that_answered() -> None:
    result = run_case(_engine(), get_template("ad-pack"), _case())
    assert result.models_used == ["fake-model"]
    report = run_benchmark(
        simulated_engine(), get_template("ad-pack"), [_case()], dataset="x", version="t"
    )
    assert "class defaults: small: fake-model" in report.to_markdown()
    assert "answered by: fake-model" in report.to_markdown()
