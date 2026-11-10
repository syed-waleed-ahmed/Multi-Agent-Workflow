"""Research agents: audience, market (wave 1) and competitors (Session 4).

All three are evidence agents (see :mod:`.evidence`). With research tools or
user sources they cite retrieved evidence, and every citation is validated.
Without them they work in labeled assumptions mode. The competitor agent may
only name competitors that appear in the brief, the user's sources or the
evidence (docs/AGENT_CATALOG.md, principle 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...domain.artifacts import Artifact
from ...domain.provenance import Evidence
from ..schemas import (
    AUDIENCE_KIND,
    COMPETITORS_KIND,
    MARKET_KIND,
    AudienceResearch,
    CompetitorResearch,
    MarketResearch,
)
from .common import BRIEF, AgentCategory, brief_of, framed_brief, make_spec
from .evidence import RESEARCH_TOOLS, SOURCES, EvidenceResearchAgent


def _basis_notes(findings: list[Any]) -> tuple[str, ...]:
    external = sum(1 for f in findings if f.basis == "external_source")
    assumed = sum(1 for f in findings if f.basis == "model_assumption")
    return (f"{len(findings)} findings: {external} cite evidence, {assumed} are model assumptions",)


class AudienceResearchAgent(EvidenceResearchAgent):
    spec = make_spec(
        "audience_research",
        "Audience research",
        "Segments, personas, pain points and objections; evidence-cited or labeled assumptions.",
        category=AgentCategory.RESEARCH,
        inputs=(BRIEF, SOURCES),
        output_kind=AUDIENCE_KIND,
        capabilities=("audience", "personas", "insights"),
        rubric=("specificity", "on_brief", "labeled_claims"),
        max_tokens=1000,
        allowed_tools=RESEARCH_TOOLS,
        max_calls=5,
        prompt_version="2",
    )
    output_model: ClassVar[type[AudienceResearch]] = AudienceResearch
    role = (
        "You are a senior audience researcher. You describe target customers concretely: "
        "who they are, what they want, what stops them buying."
    )
    research_goal = "who buys products like this and what they care about"
    shape = (
        '{"summary": "...", "personas": [{"name": "...", "description": "...", '
        '"motivations": ["..."], "pain_points": ["..."], "objections": ["..."], '
        '"preferred_channels": ["..."]}], '
        '"insights": [{"statement": "...", "basis": "model_assumption", "evidence_ids": []}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            "Task: describe the target audience as 2-3 personas, then list 3-6 insights "
            "that matter for this campaign."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return _basis_notes(value.insights)


class MarketResearchAgent(EvidenceResearchAgent):
    spec = make_spec(
        "market_research",
        "Market research",
        "Category dynamics, trends and positioning angles; evidence-cited or labeled assumptions.",
        category=AgentCategory.RESEARCH,
        inputs=(BRIEF, SOURCES),
        output_kind=MARKET_KIND,
        capabilities=("market", "trends", "angles"),
        rubric=("relevance", "on_brief", "labeled_claims"),
        max_tokens=900,
        allowed_tools=RESEARCH_TOOLS,
        max_calls=5,
        prompt_version="2",
    )
    output_model: ClassVar[type[MarketResearch]] = MarketResearch
    role = (
        "You are a market strategist. You explain the category a product competes in and "
        "where the opportunity is, separating what is known from what is assumed."
    )
    research_goal = "the product's category, its size and its current trends"
    shape = (
        '{"category_summary": "...", '
        '"trends": [{"statement": "...", "basis": "model_assumption", "evidence_ids": []}], '
        '"competitor_angles": [{"statement": "...", "basis": "model_assumption"}], '
        '"positioning_angles": ["...", "...", "..."]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            "Task: summarise the product's category, 2-4 relevant trends, how competitors "
            "typically position themselves, and 3-5 positioning angles this campaign could take."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return _basis_notes([*value.trends, *value.competitor_angles])


class CompetitorResearchAgent(EvidenceResearchAgent):
    spec = make_spec(
        "competitor_research",
        "Competitor research",
        "Profiles of named competitors only (from the brief, sources or evidence), with gaps.",
        category=AgentCategory.RESEARCH,
        inputs=(BRIEF, SOURCES),
        output_kind=COMPETITORS_KIND,
        capabilities=("competitors", "comparison", "opportunities"),
        rubric=("accuracy", "on_brief", "labeled_claims"),
        max_tokens=1200,
        allowed_tools=RESEARCH_TOOLS,
        max_calls=5,
    )
    output_model: ClassVar[type[CompetitorResearch]] = CompetitorResearch
    role = (
        "You are a competitive-intelligence analyst. You compare real, named competitors "
        "fairly and never invent companies, products or figures."
    )
    research_goal = "the main competitors and alternatives to this product"
    shape = (
        '{"competitors": [{"name": "...", "summary": "...", "positioning": {"statement": "...", '
        '"basis": "external_source", "evidence_ids": ["ev-..."]}, "strengths": [], '
        '"weaknesses": []}], "opportunities": [{"statement": "...", "basis": "model_assumption"}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            "Task: profile up to 5 competitors that are explicitly named in the brief, the "
            "user's sources or the research evidence: how each positions itself, its strengths "
            "and weaknesses. If none are named there, return an empty competitors list. Then "
            "list 1-5 opportunities for this product."
        )

    def extra_context(
        self, inputs: Mapping[str, Artifact], evidence: list[Evidence]
    ) -> dict[str, Any]:
        reference = " ".join(
            [brief_of(inputs).as_prompt_context(), *(f"{e.title} {e.excerpt}" for e in evidence)]
        )
        return {"reference_text": reference.casefold()}

    def notes(self, value: Any) -> tuple[str, ...]:
        named = ", ".join(c.name for c in value.competitors) or "none found"
        return (f"competitors: {named}",)
