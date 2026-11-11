"""The Evaluation Lab (Session 8): datasets through workflows, scored, compared, recorded.

* **Datasets** are JSON lists of cases. Each case has an ``id``, a ``category``,
  a ``brief``, and optionally ``sources`` (the user's own documents) and a
  ``brand``. The bundled ones live in ``campaign_forge/evaluation/datasets``.
* **Scoring** uses the same deterministic checks as the critic and the eval
  harness (schema, injection echo, grounding, labeled claims, citations, report
  sections). A case passes when it completes with no critical failure.
* **Every report records** when it ran, whether it was simulated or live, the
  provider, and the model behind each model class. Benchmarks are only ever
  produced by real runs, and nothing is estimated after the fact.
* **Regressions** compare the stable metrics (pass rate, calls and tokens per
  case, newly failing cases) with a stored baseline. Durations vary and are
  never compared.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.brand import BrandProfile
from ..domain.sources import SourceBundle, SourceDocument
from ..evaluation.checks import check_artifact
from ..exceptions import CampaignForgeError, ConfigurationError
from ..models import CampaignBrief
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.engine import WorkflowEngine
from ..orchestration.state import RunRecord
from ..providers.base import ModelClass
from ..security.redaction import redact

#: Price per 1,000 tokens by provider. Every supported provider is used on its free
#: tier, so the estimate is $0.00; a paid tier would need its own price here.
PRICE_PER_1K_TOKENS: dict[str, float] = {"fake": 0.0, "groq": 0.0, "gemini": 0.0}
#: Relative growth in calls or tokens per case that counts as a regression.
GROWTH_TOLERANCE = 0.2
BUNDLED_DATASETS = ("corpus", "injection")


class LabCase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    category: str = "normal"
    notes: str = ""
    brief: CampaignBrief
    sources: list[SourceDocument] = Field(default_factory=list)
    brand: BrandProfile | None = None


class CheckView(BaseModel):
    name: str
    passed: bool
    critical: bool
    detail: str = ""


class CaseResult(BaseModel):
    id: str
    category: str
    status: str
    completed: bool
    passed: bool
    duration_s: float
    llm_calls: int = 0
    tokens: int = 0
    retries: int = 0
    review_score: float | None = None
    #: The models that actually answered (cap-aware routing may use several).
    models_used: list[str] = Field(default_factory=list)
    checks: list[CheckView] = Field(default_factory=list)
    error: str | None = None

    @property
    def warnings(self) -> list[CheckView]:
        return [c for c in self.checks if not c.passed and not c.critical]

    def problem(self) -> str:
        if self.error:
            return self.error
        failed = [c for c in self.checks if not c.passed and c.critical]
        return f"{failed[0].name}: {failed[0].detail}" if failed else ""


class Summary(BaseModel):
    cases: int
    passed: int
    success_rate: float
    failure_rate: float
    completion_rate: float
    avg_duration_s: float
    total_calls: int
    avg_calls: float
    total_tokens: int
    avg_tokens: float
    retries: int
    avg_review_score: float | None
    warnings: int
    estimated_cost_usd: float


class BenchmarkReport(BaseModel):
    """A dated record of one dataset run through one workflow."""

    workflow: str
    dataset: str
    mode: Literal["simulated", "live"]
    provider: str
    models: dict[str, str]
    version: str
    started_at: datetime
    finished_at: datetime
    summary: Summary
    results: list[CaseResult]

    def to_markdown(self) -> str:
        s = self.summary
        review = "n/a" if s.avg_review_score is None else f"{s.avg_review_score:.2f}"
        rows = "\n".join(
            f"| {r.id} | {r.category} | {'pass' if r.passed else 'FAIL'} | {r.duration_s:.1f} | "
            f"{r.llm_calls} | {r.tokens:,} | {r.retries} | "
            f"{'' if r.review_score is None else f'{r.review_score:.2f}'} | "
            f"{r.problem().replace('|', '/')[:80]} |"
            for r in self.results
        )
        models = ", ".join(f"{k}: {v}" for k, v in self.models.items())
        return (
            f"## {self.workflow} on {self.dataset} ({self.mode})\n\n"
            f"- Run: {self.started_at:%Y-%m-%d %H:%M} UTC, Campaign Forge {self.version}\n"
            f"- Provider: {self.provider}; class defaults: {models}; answered by: "
            f"{', '.join(sorted({m for r in self.results for m in r.models_used})) or 'n/a'}\n"
            f"- Passed {s.passed}/{s.cases} ({s.success_rate:.0%}); completed "
            f"{s.completion_rate:.0%}; {s.warnings} warning(s)\n"
            f"- Per case: {s.avg_duration_s:.1f} s, {s.avg_calls:.1f} calls, "
            f"{s.avg_tokens:,.0f} tokens; {s.retries} retries in total; review {review}\n"
            f"- Estimated cost: ${s.estimated_cost_usd:.2f}\n\n"
            "| Case | Category | Result | Seconds | Calls | Tokens | Retries | Review | Problem |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows + "\n"
        )


# --------------------------------------------------------------------------- #
# Datasets
# --------------------------------------------------------------------------- #
def load_dataset(source: str | Path) -> tuple[str, list[LabCase]]:
    """A bundled dataset by name (``corpus``, ``injection``) or a JSON file path.

    Raises:
        ConfigurationError: unknown name, unreadable file, or invalid cases.
    """
    name = str(source)
    if name in BUNDLED_DATASETS:
        package = resources.files("campaign_forge.evaluation").joinpath("datasets")
        raw = package.joinpath(f"{name}.json").read_text(encoding="utf-8")
    else:
        path = Path(source)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            known = ", ".join(BUNDLED_DATASETS)
            raise ConfigurationError(
                f"Cannot read dataset {name!r} ({exc}). Bundled datasets: {known}."
            ) from exc
        name = path.stem
    try:
        data = json.loads(raw)
        cases = [LabCase.model_validate(item) for item in data]
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(f"Dataset {name!r} is not valid: {exc}") from exc
    return name, cases


def _inputs(definition: WorkflowDefinition, case: LabCase) -> dict[str, BaseModel]:
    inputs: dict[str, BaseModel] = {"brief": case.brief}
    accepts = set(definition.inputs) | set(definition.optional_inputs)
    documents = list(case.sources)
    if not documents and "sources" in definition.inputs:
        # A workflow that requires your own text gets the brief's description as its text.
        documents = [SourceDocument(name="brief", content=case.brief.product_description)]
    if documents and "sources" in accepts:
        inputs["sources"] = SourceBundle(documents=tuple(documents))
    if case.brand is not None and "brand" in accepts:
        inputs["brand"] = case.brand
    return inputs


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
def _retries(record: RunRecord, resumes: int) -> int:
    attempts = sum(max(0, s.attempts - 1) for s in record.steps.values())
    truncations = sum(1 for e in record.events if "truncated" in e.message)
    return attempts + truncations + resumes


def _score(case: LabCase, record: RunRecord, ok: bool, duration: float, resumes: int) -> CaseResult:
    checks = [
        CheckView(
            name="completed",
            passed=ok,
            critical=True,
            detail=f"run {record.status.value}: {record.status_reason or ''}".strip(),
        )
    ]
    for name, artifact in sorted(record.outputs().items()):
        for check in check_artifact(artifact, case.brief):
            checks.append(
                CheckView(
                    name=f"{name}.{check.name}",
                    passed=check.passed,
                    critical=check.critical,
                    detail=check.detail,
                )
            )
    scores = []
    for step_id, state in record.steps.items():
        if state.status.value == "failed":
            checks.append(
                CheckView(
                    name=f"{step_id}.step", passed=False, critical=False, detail=state.error or ""
                )
            )
        if state.review_score is not None:
            scores.append(state.review_score)
            if state.review_score < 0.7:
                checks.append(
                    CheckView(
                        name=f"{step_id}.review",
                        passed=False,
                        critical=False,
                        detail=f"critic score {state.review_score:.2f}",
                    )
                )
    passed = all(c.passed for c in checks if c.critical)
    return CaseResult(
        id=case.id,
        category=case.category,
        status=record.status.value,
        completed=ok,
        passed=passed,
        duration_s=round(duration, 2),
        llm_calls=record.llm_calls,
        tokens=record.usage.total_tokens,
        retries=_retries(record, resumes),
        review_score=round(sum(scores) / len(scores), 3) if scores else None,
        models_used=sorted(
            {a.produced_by.model for a in record.artifacts.values() if a.produced_by.model}
        ),
        checks=checks,
    )


def run_case(
    engine: WorkflowEngine,
    definition: WorkflowDefinition,
    case: LabCase,
    *,
    max_resumes: int = 3,
    clock: Callable[[], float] = time.perf_counter,
) -> CaseResult:
    start = clock()
    try:
        result = engine.start(definition, _inputs(definition, case))
        first = result.record.status
        result = engine.run_until_settled(result, max_resumes=max_resumes)
        resumes = 0 if result.record.status is first else 1
        return _score(case, result.record, result.ok, clock() - start, resumes)
    except Exception as exc:  # noqa: BLE001 - one broken case must not end the benchmark
        return CaseResult(
            id=case.id,
            category=case.category,
            status="error",
            completed=False,
            passed=False,
            duration_s=round(clock() - start, 2),
            # Reports are saved and may be committed: never with a key in them.
            error=redact(f"{type(exc).__name__}: {exc}", engine.router.secret_values),
        )


def _models(engine: WorkflowEngine) -> dict[str, str]:
    models = {}
    for model_class in ModelClass:
        try:
            # The class default: a short answer never triggers cap-aware routing (ADR-035).
            route = engine.router.resolve(model_class=model_class, output_tokens=1)
            models[model_class.value] = route.model
        except CampaignForgeError as exc:
            models[model_class.value] = f"unavailable ({exc})"
    return models


def summarise(results: Sequence[CaseResult], provider: str) -> Summary:
    count = len(results) or 1
    passed = sum(r.passed for r in results)
    reviews = [r.review_score for r in results if r.review_score is not None]
    tokens = sum(r.tokens for r in results)
    return Summary(
        cases=len(results),
        passed=passed,
        success_rate=round(passed / count, 4),
        failure_rate=round(1 - passed / count, 4),
        completion_rate=round(sum(r.completed for r in results) / count, 4),
        avg_duration_s=round(sum(r.duration_s for r in results) / count, 2),
        total_calls=sum(r.llm_calls for r in results),
        avg_calls=round(sum(r.llm_calls for r in results) / count, 2),
        total_tokens=tokens,
        avg_tokens=round(tokens / count, 1),
        retries=sum(r.retries for r in results),
        avg_review_score=round(sum(reviews) / len(reviews), 3) if reviews else None,
        warnings=sum(len(r.warnings) for r in results),
        estimated_cost_usd=round(tokens / 1000 * PRICE_PER_1K_TOKENS.get(provider, 0.0), 4),
    )


def run_benchmark(
    engine: WorkflowEngine,
    definition: WorkflowDefinition,
    cases: Sequence[LabCase],
    *,
    dataset: str,
    version: str,
    max_resumes: int = 3,
    on_case: Callable[[CaseResult], None] | None = None,
) -> BenchmarkReport:
    """Run every case and record the result with its date, mode, provider and models."""
    provider = engine.router.global_provider
    started = datetime.now(timezone.utc)
    results = []
    for case in cases:
        result = run_case(engine, definition, case, max_resumes=max_resumes)
        results.append(result)
        if on_case is not None:
            on_case(result)
    return BenchmarkReport(
        workflow=f"{definition.id}@{definition.version}",
        dataset=dataset,
        mode="simulated" if provider == "fake" else "live",
        provider=provider,
        models=_models(engine),
        version=version,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        summary=summarise(results, provider),
        results=results,
    )


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #
def compare(report: BenchmarkReport, baseline: BenchmarkReport) -> list[str]:
    """Regressions of ``report`` against ``baseline`` (empty when there are none).

    Raises:
        ConfigurationError: if the baseline is for another workflow or dataset.
    """
    if (report.workflow, report.dataset) != (baseline.workflow, baseline.dataset):
        raise ConfigurationError(
            f"The baseline is for {baseline.workflow} on {baseline.dataset}, not "
            f"{report.workflow} on {report.dataset}."
        )
    problems = []
    now, then = report.summary, baseline.summary
    if now.success_rate < then.success_rate:
        problems.append(f"pass rate fell from {then.success_rate:.0%} to {now.success_rate:.0%}")
    for label, current, previous in (
        ("calls per case", now.avg_calls, then.avg_calls),
        ("tokens per case", now.avg_tokens, then.avg_tokens),
    ):
        if previous and current > previous * (1 + GROWTH_TOLERANCE):
            problems.append(f"{label} grew from {previous:,.1f} to {current:,.1f}")
    before = {r.id for r in baseline.results if r.passed}
    newly = sorted(r.id for r in report.results if not r.passed and r.id in before)
    if newly:
        problems.append(f"newly failing: {', '.join(newly)}")
    return problems


def bundled_baseline(workflow_id: str, dataset: str) -> BenchmarkReport | None:
    """The offline baseline shipped with the package for this workflow and dataset, if any."""
    package = resources.files("campaign_forge.evaluation").joinpath("baselines")
    candidate = package.joinpath(f"{workflow_id}.{dataset}.json")
    if not candidate.is_file():
        return None
    return BenchmarkReport.model_validate_json(candidate.read_text(encoding="utf-8"))


def load_report(path: str | Path) -> BenchmarkReport:
    try:
        return BenchmarkReport.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read benchmark report {path}: {exc}") from exc
