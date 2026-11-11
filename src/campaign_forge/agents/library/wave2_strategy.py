"""Strategy and analytics agents, wave 2: funnel, growth, experiments, performance.

The model proposes; code computes (ADR-011). ICE scores and ranks, experiment
sample sizes, and CTR, CVR, CPC, CPA and ROAS are never written by a model.
``performance_analyst`` works only from the user's own data and refuses
without it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.sources import SOURCES_KIND, SourceBundle
from ...exceptions import AgentError
from ...providers.base import CompletionRequest
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import AUDIENCE_KIND, BRIEF_KIND, PLAN_KIND, POSITIONING_KIND
from ..schemas_wave2 import (
    EXPERIMENTS_KIND,
    FUNNEL_KIND,
    GROWTH_KIND,
    LANDING_KIND,
    PERFORMANCE_KIND,
    ExperimentPlan,
    FunnelPlan,
    GrowthBacklog,
    PerformanceNarrative,
    PerformanceReport,
    parse_performance_csv,
    totals,
)
from .common import (
    BRIEF,
    OPTIONAL_BRAND,
    AgentCategory,
    StructuredAgent,
    context,
    framed_brand,
    framed_brief,
    make_spec,
    produced_by,
)

OPTIONAL_POSITIONING = InputSpec(name="positioning", kind=POSITIONING_KIND, required=False)
OPTIONAL_AUDIENCE = InputSpec(name="audience", kind=AUDIENCE_KIND, required=False)
OPTIONAL_PLAN = InputSpec(name="plan", kind=PLAN_KIND, required=False)
OPTIONAL_PERFORMANCE = InputSpec(name="performance", kind=PERFORMANCE_KIND, required=False)
OPTIONAL_LANDING = InputSpec(name="landing", kind=LANDING_KIND, required=False)
DATA = InputSpec(name="sources", kind=SOURCES_KIND)


class FunnelStrategistAgent(StructuredAgent):
    spec = make_spec(
        "funnel_strategist",
        "Funnel strategist",
        "Funnel stages with content, conversion points and metrics, plus lead-magnet concepts.",
        category=AgentCategory.STRATEGY,
        inputs=(BRIEF, OPTIONAL_POSITIONING, OPTIONAL_AUDIENCE, OPTIONAL_BRAND),
        output_kind=FUNNEL_KIND,
        capabilities=("funnel_design", "lead_magnets", "conversion_paths"),
        rubric=("on_brief", "completeness", "actionability"),
        max_tokens=1000,
        reviewable=True,
    )
    output_model: ClassVar[type[FunnelPlan]] = FunnelPlan
    role = (
        "You are a demand-generation strategist. You design funnels where every stage has a "
        "clear job, a conversion point and a metric."
    )
    shape = (
        '{"stages": [{"name": "Awareness", "goal": "...", "content": ["..."], '
        '"conversion_point": "...", "metric": "..."}], "lead_magnets": [{"title": "...", '
        '"format": "...", "promise": "...", "stage": "Awareness"}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{context(inputs, 'positioning', 'audience')}\n\n"
            "Task: design a 3-5 stage funnel for this campaign. For each stage give its goal, "
            "1-4 content pieces, one conversion point and one metric. Then propose 1-3 lead "
            "magnets, each naming the stage it serves (use the stage names exactly)."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class GrowthStrategistAgent(StructuredAgent):
    spec = make_spec(
        "growth_strategist",
        "Growth strategist",
        "A backlog of growth experiments; the model rates impact, confidence and ease, and "
        "code computes ICE scores and the ranking.",
        category=AgentCategory.STRATEGY,
        inputs=(BRIEF, OPTIONAL_PLAN, OPTIONAL_PERFORMANCE),
        output_kind=GROWTH_KIND,
        capabilities=("growth_strategy", "experiment_backlog", "ice_scoring"),
        rubric=("on_brief", "actionability", "variety"),
        max_tokens=1000,
        reviewable=True,
    )
    output_model: ClassVar[type[GrowthBacklog]] = GrowthBacklog
    role = (
        "You are a growth lead. You propose specific, testable growth ideas and rate them "
        "honestly; you never pad the list with generic advice."
    )
    shape = (
        '{"ideas": [{"name": "...", "hypothesis": "...", "channel": "...", "impact": 7, '
        '"confidence": 5, "ease": 6}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'plan', 'performance')}\n\n"
            "Task: propose 5-8 growth experiments for this product. Give each a name, a "
            "one-sentence hypothesis, a channel, and integer ratings from 1 to 10 for impact, "
            "confidence and ease. Do not compute scores or rank them; that is done for you."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class ExperimentDesignerAgent(StructuredAgent):
    spec = make_spec(
        "experiment_designer",
        "Experiment designer",
        "A/B tests with hypotheses, variants and a primary metric; code computes the sample "
        "size, and baselines are labeled assumptions unless measured.",
        category=AgentCategory.ANALYTICS,
        inputs=(BRIEF, OPTIONAL_PERFORMANCE, OPTIONAL_LANDING),
        output_kind=EXPERIMENTS_KIND,
        capabilities=("ab_testing", "experiment_design", "sample_size"),
        rubric=("on_brief", "actionability", "rigor"),
        max_tokens=900,
    )
    output_model: ClassVar[type[ExperimentPlan]] = ExperimentPlan
    role = (
        "You are an experimentation specialist. Each test changes one thing, has a single "
        "primary metric, and states a realistic baseline and minimum detectable effect."
    )
    shape = (
        '{"experiments": [{"name": "...", "hypothesis": "...", "control": "...", '
        '"variant": "...", "primary_metric": "conversion rate", "baseline_rate": 0.03, '
        '"minimum_detectable_effect": 0.15}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'performance', 'landing')}\n\n"
            "Task: design 2-3 A/B tests for this campaign. For each, give the hypothesis, the "
            "control and the variant (one change), a primary metric (a click-through or "
            "conversion rate), the baseline rate as a decimal and the smallest relative lift "
            "worth detecting as a decimal. Do not compute sample sizes."
        )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        rates: dict[str, float] = {}
        if "performance" in inputs:
            total = inputs["performance"].as_model(PerformanceReport).total
            rates = {k: v for k, v in (("ctr", total.ctr), ("cvr", total.cvr)) if v is not None}
        # Always pass the key: code, not the model, decides which baselines are measured.
        return self.produce(inputs, ctx, validation_context={"measured_rates": rates})

    def notes(self, value: Any) -> tuple[str, ...]:
        return (f"baselines {value.baseline_source}; {value.digest()}",)


class PerformanceAnalystAgent:
    """Metrics from the user's CSV, computed by code; the model writes only the narrative."""

    spec = make_spec(
        "performance_analyst",
        "Performance analyst",
        "Computes CTR, CVR, CPC, CPA and ROAS from your CSV and explains them; needs data.",
        category=AgentCategory.ANALYTICS,
        inputs=(DATA, InputSpec(name="brief", kind=BRIEF_KIND, required=False)),
        output_kind=PERFORMANCE_KIND,
        capabilities=("campaign_analytics", "performance_review"),
        rubric=("accuracy", "actionability"),
        max_tokens=900,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        bundle = inputs["sources"].as_model(SourceBundle)
        rows = None
        source = ""
        problems = []
        for document in bundle.documents:
            try:
                rows = parse_performance_csv(document.content)
                source = document.name
                break
            except ValueError as exc:
                problems.append(f"{document.name}: {exc}")
        if rows is None:
            raise AgentError(
                self.spec.id,
                "needs performance data: a CSV with channel, impressions, clicks, conversions "
                "and spend columns (revenue optional). " + "; ".join(problems),
            )
        total = totals(rows)
        draft = PerformanceReport(
            source=source, rows=rows, total=total, summary="", insights=[], recommendations=[]
        )
        brief_block = f"Campaign brief:\n{framed_brief(inputs)}\n\n" if "brief" in inputs else ""
        system = with_untrusted_policy(
            "You are a marketing analyst. You interpret metrics that have already been "
            "computed, cite only those numbers, and never calculate new ones. Respond with one "
            "JSON object."
        )
        user = (
            f"{brief_block}Computed metrics:\n"
            f"{frame_untrusted(draft.to_markdown(), label='computed metrics')}\n\n"
            "Task: write a short summary, 2-4 insights and 2-4 recommendations. Use only the "
            "numbers in the table.\n\n"
            'Return ONLY a JSON object: {"summary": "...", "insights": ["..."], '
            '"recommendations": ["..."]}'
        )
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), PerformanceNarrative
        )
        narrative = result.value
        report = draft.model_copy(
            update={
                "summary": narrative.summary,
                "insights": list(narrative.insights),
                "recommendations": list(narrative.recommendations),
            }
        )
        artifact = Artifact.from_model(
            PERFORMANCE_KIND,
            report,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        return AgentOutput(artifact=artifact, notes=(report.digest(),))


__all__ = [
    "ExperimentDesignerAgent",
    "FunnelStrategistAgent",
    "GrowthStrategistAgent",
    "PerformanceAnalystAgent",
]
