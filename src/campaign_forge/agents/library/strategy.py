"""Strategy agents (wave 1): positioning and the marketing plan."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import InputSpec
from ...domain.artifacts import Artifact
from ..schemas import (
    AUDIENCE_KIND,
    MARKET_KIND,
    PLAN_KIND,
    POSITIONING_KIND,
    MarketingPlan,
    Positioning,
)
from .common import (
    BRIEF,
    AgentCategory,
    StructuredAgent,
    brief_of,
    context,
    framed_brief,
    make_spec,
)

AUDIENCE = InputSpec(name="audience", kind=AUDIENCE_KIND)
MARKET = InputSpec(name="market", kind=MARKET_KIND, required=False)
POSITIONING = InputSpec(name="positioning", kind=POSITIONING_KIND)


class PositioningAgent(StructuredAgent):
    spec = make_spec(
        "positioning",
        "Positioning",
        "Positioning statement, value propositions, messaging pillars and benefit ladder.",
        category=AgentCategory.STRATEGY,
        inputs=(BRIEF, AUDIENCE, MARKET),
        output_kind=POSITIONING_KIND,
        capabilities=("positioning", "messaging", "value_proposition"),
        rubric=("distinctiveness", "on_brief", "clarity"),
        max_tokens=900,
    )
    output_model: ClassVar[type[Positioning]] = Positioning
    role = (
        "You are a brand positioning strategist. You turn product facts and audience needs "
        "into one sharp, defensible position."
    )
    shape = (
        '{"positioning_statement": "For [audience] who [need], [product] is the [category] '
        'that [benefit], unlike [alternative].", "value_propositions": ["...", "..."], '
        '"messaging_pillars": [{"name": "...", "message": "...", "proof_points_needed": ["..."]}], '
        '"benefit_ladder": [{"feature": "...", "benefit": "...", "emotional_payoff": "..."}], '
        '"differentiators": ["..."]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'audience', 'market')}\n\n"
            "Task: write the positioning statement, 2-4 value propositions, 2-4 messaging "
            "pillars (with the proof points we would need to back each claim), a benefit "
            "ladder from product features, and 1-3 differentiators. Do not invent product "
            "features beyond the brief."
        )


class MarketingStrategistAgent(StructuredAgent):
    spec = make_spec(
        "marketing_strategist",
        "Marketing strategist",
        "Objectives, KPIs, channel mix and phases; effort shares are computed by code.",
        category=AgentCategory.STRATEGY,
        inputs=(BRIEF, POSITIONING, InputSpec(name="audience", kind=AUDIENCE_KIND, required=False)),
        output_kind=PLAN_KIND,
        capabilities=("strategy", "channel_mix", "kpis", "timeline"),
        rubric=("feasibility", "on_brief", "measurability"),
        max_tokens=1000,
    )
    output_model: ClassVar[type[MarketingPlan]] = MarketingPlan
    role = (
        "You are a pragmatic marketing strategist. You plan campaigns that a small team "
        "can execute and measure."
    )
    shape = (
        '{"objectives": ["..."], "kpis": ["..."], "channels": [{"channel": "...", "role": "...", '
        '"weight": 5, "kpis": ["..."]}], "phases": [{"name": "...", "weeks": 2, "focus": "...", '
        '"activities": ["..."]}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        channels = ", ".join(brief_of(inputs).channels)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'positioning', 'audience')}\n\n"
            f"Task: plan the campaign. Use these channels: {channels} (each once). For each "
            "channel give its role, a relative effort weight from 1 to 10, and 1-3 KPIs. "
            "Add 1-3 measurable objectives and 2-4 phases with durations in weeks. Do not "
            "compute percentages; weights are enough."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        shares = ", ".join(f"{c.channel} {c.share_percent}%" for c in value.channels)
        return (f"effort shares computed from weights: {shares}",)
