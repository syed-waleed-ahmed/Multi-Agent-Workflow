"""Research agents (wave 1): audience and market.

Until the Session 4 research tools exist, these agents work in **assumptions
mode**: every finding is labeled ``user_provided`` (it restates the brief) or
``model_assumption``. The output schema offers no ``external_source`` option, so
the model cannot claim research it did not do (docs/AGENT_CATALOG.md, principle 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...domain.artifacts import Artifact
from ..schemas import AUDIENCE_KIND, MARKET_KIND, AudienceResearch, MarketResearch
from .common import BRIEF, AgentCategory, StructuredAgent, framed_brief, make_spec

_ASSUMPTIONS_NOTE = (
    "No external research sources are available in this run. Label every finding "
    '"user_provided" if it restates the brief, otherwise "model_assumption". '
    "Do not cite statistics, studies, or named sources."
)


def _assumption_notes(findings: list[Any]) -> tuple[str, ...]:
    assumed = sum(1 for f in findings if f.basis == "model_assumption")
    return (f"assumptions mode: {assumed} of {len(findings)} findings are model assumptions",)


class AudienceResearchAgent(StructuredAgent):
    spec = make_spec(
        "audience_research",
        "Audience research",
        "Segments, personas, pain points, desires and objections, each claim labeled.",
        category=AgentCategory.RESEARCH,
        inputs=(BRIEF,),
        output_kind=AUDIENCE_KIND,
        capabilities=("audience", "personas", "insights"),
        rubric=("specificity", "on_brief", "labeled_claims"),
        max_tokens=1000,
    )
    output_model: ClassVar[type[AudienceResearch]] = AudienceResearch
    role = (
        "You are a senior audience researcher. You describe target customers concretely: "
        "who they are, what they want, what stops them buying."
    )
    shape = (
        '{"summary": "...", "personas": [{"name": "...", "description": "...", '
        '"motivations": ["..."], "pain_points": ["..."], "objections": ["..."], '
        '"preferred_channels": ["..."]}], '
        '"insights": [{"statement": "...", "basis": "model_assumption"}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            "Task: describe the target audience as 2-3 personas, then list 3-6 insights "
            f"that matter for this campaign.\n{_ASSUMPTIONS_NOTE}"
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return _assumption_notes(value.insights)


class MarketResearchAgent(StructuredAgent):
    spec = make_spec(
        "market_research",
        "Market research",
        "Category dynamics, trends and positioning angles (assumptions mode until S4).",
        category=AgentCategory.RESEARCH,
        inputs=(BRIEF,),
        output_kind=MARKET_KIND,
        capabilities=("market", "trends", "angles"),
        rubric=("relevance", "on_brief", "labeled_claims"),
        max_tokens=900,
    )
    output_model: ClassVar[type[MarketResearch]] = MarketResearch
    role = (
        "You are a market strategist. You explain the category a product competes in and "
        "where the opportunity is, separating what is known from what is assumed."
    )
    shape = (
        '{"category_summary": "...", '
        '"trends": [{"statement": "...", "basis": "model_assumption"}], '
        '"competitor_angles": [{"statement": "...", "basis": "model_assumption"}], '
        '"positioning_angles": ["...", "...", "..."]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            "Task: summarise the product's category, 2-4 relevant trends, how competitors "
            "typically position themselves (no named competitors or figures), and 3-5 "
            f"positioning angles this campaign could take.\n{_ASSUMPTIONS_NOTE}"
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return _assumption_notes([*value.trends, *value.competitor_angles])
