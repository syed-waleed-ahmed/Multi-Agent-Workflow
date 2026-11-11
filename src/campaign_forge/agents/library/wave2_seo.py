"""SEO agents, wave 2: keyword research, content planning, on-page optimisation.

* ``keyword_research`` never states a search volume itself. Volumes come only
  from a CSV the user supplies (``keyword`` and ``volume`` columns). Without
  one the field stays empty, and the artifact says so.
* ``seo_optimizer`` scores a page with deterministic on-page checks computed by
  code. The model only turns the failed checks into recommendations.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.sources import SourceBundle
from ...exceptions import AgentError
from ...providers.base import CompletionRequest, ModelClass
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas_wave2 import (
    ARTICLE_KIND,
    KEYWORDS_KIND,
    SEO_AUDIT_KIND,
    SEO_PLAN_KIND,
    Article,
    KeywordPlan,
    SeoAudit,
    SeoContentPlan,
    SeoRecommendations,
    keyword_volumes,
    seo_checks,
)
from .common import (
    BRIEF,
    AgentCategory,
    StructuredAgent,
    context,
    framed_brief,
    make_spec,
    produced_by,
)
from .wave2_content import OPTIONAL_KEYWORDS, OPTIONAL_SOURCES, framed_sources

KEYWORDS = InputSpec(name="keywords", kind=KEYWORDS_KIND)
OPTIONAL_ARTICLE = InputSpec(name="article", kind=ARTICLE_KIND, required=False)


class KeywordResearchAgent(StructuredAgent):
    spec = make_spec(
        "keyword_research",
        "Keyword research",
        "Seed keywords, search intent and topic clusters. Volumes only from your CSV, never "
        "invented.",
        category=AgentCategory.SEO,
        inputs=(BRIEF, OPTIONAL_SOURCES),
        output_kind=KEYWORDS_KIND,
        capabilities=("keyword_research", "keyword_clustering", "search_intent"),
        rubric=("on_brief", "relevance", "coverage"),
        max_tokens=900,
    )
    output_model: ClassVar[type[KeywordPlan]] = KeywordPlan
    role = (
        "You are an SEO strategist. You group keywords by search intent. You never state "
        "search volumes: you do not have that data."
    )
    shape = (
        '{"seed_keywords": ["..."], "clusters": [{"name": "...", "intent": "commercial", '
        '"keywords": ["...", "..."]}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_sources(inputs, 3000)}"
            "Task: propose 2-5 seed keywords and 3-6 topic clusters of 3-8 related keywords "
            "each, with the search intent of each cluster (informational, commercial, "
            "transactional or navigational). Put the most important cluster first and its "
            "most important keyword first. Do not include search volumes."
        )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        volumes: dict[str, int] = {}
        source = None
        if "sources" in inputs:
            for document in inputs["sources"].as_model(SourceBundle).documents:
                found = keyword_volumes(document.content)
                if found:
                    volumes.update(found)
                    source = f"user CSV: {document.name}"
        note = [f"volumes from {source}"] if source else ["no keyword volumes supplied"]
        return self.produce(
            inputs,
            ctx,
            validation_context={"keyword_volumes": volumes, "volume_source": source},
            notes=note,
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class SeoContentPlannerAgent(StructuredAgent):
    spec = make_spec(
        "seo_content_planner",
        "SEO content planner",
        "Content briefs with outlines and internal links, scheduled one per week by code.",
        category=AgentCategory.SEO,
        inputs=(BRIEF, KEYWORDS),
        output_kind=SEO_PLAN_KIND,
        capabilities=("seo_content_planning", "content_calendar"),
        rubric=("on_brief", "coverage", "actionability"),
        max_tokens=1200,
    )
    output_model: ClassVar[type[SeoContentPlan]] = SeoContentPlan
    role = (
        "You are an SEO content lead. Each piece you plan targets one keyword and one intent, "
        "and links to related pieces."
    )
    shape = (
        '{"pieces": [{"title": "...", "target_keyword": "...", "intent": "informational", '
        '"outline": ["...", "...", "..."], "internal_links": ["..."]}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{context(inputs, 'keywords')}\n\n"
            "Task: plan 4-6 content pieces from these keyword clusters, most important first. "
            "Give each a title (max 70 characters), its target keyword, the intent, a 3-6 point "
            "outline, and up to 3 internal links (titles of other pieces in this plan)."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class SeoOptimizerAgent:
    """On-page checks computed by code, then model recommendations for what failed."""

    spec = make_spec(
        "seo_optimizer",
        "SEO optimizer",
        "Deterministic on-page checks (title, meta, headings, length, keyword use) plus "
        "recommendations; audits an article or your own page text.",
        category=AgentCategory.SEO,
        inputs=(OPTIONAL_ARTICLE, OPTIONAL_SOURCES, OPTIONAL_KEYWORDS),
        output_kind=SEO_AUDIT_KIND,
        capabilities=("seo_audit", "content_optimization"),
        rubric=("accuracy", "actionability"),
        max_tokens=600,
        model_class=ModelClass.SMALL,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        keyword = None
        if "keywords" in inputs:
            keyword = inputs["keywords"].as_model(KeywordPlan).primary_keyword
        if "article" in inputs:
            article = inputs["article"].as_model(Article)
            page, title, meta, body = (
                article.title,
                article.title,
                article.meta_description,
                (article.markdown_body()),
            )
            keyword = article.target_keyword or keyword
        elif "sources" in inputs and inputs["sources"].as_model(SourceBundle).documents:
            document = inputs["sources"].as_model(SourceBundle).documents[0]
            lines = [line.strip() for line in document.content.splitlines() if line.strip()]
            first = lines[0].lstrip("# ").strip() if lines else document.name
            page, title, meta, body = document.name, first, "", document.content
        else:
            raise AgentError(self.spec.id, "needs an article or your own page text to audit")
        checks = seo_checks(title, meta, body, keyword)
        failed = [c for c in checks if not c.passed]
        score = round(sum(c.passed for c in checks) / len(checks), 3)
        system = with_untrusted_policy(
            "You are an SEO editor. You give specific, actionable fixes. Respond with one JSON "
            "object."
        )
        summary = "\n".join(
            f"- {c.name}: {'ok' if c.passed else 'FAILED'} ({c.detail})" for c in checks
        )
        user = (
            f"Page:\n{frame_untrusted(f'{title}{chr(10)}{body[:4000]}', label='page')}\n\n"
            f"Automated on-page checks:\n{summary}\n\n"
            f"Task: give 2-5 specific recommendations, starting with the failed checks"
            f"{' and the target keyword ' + repr(keyword) if keyword else ''}.\n\n"
            'Return ONLY a JSON object: {"recommendations": ["..."]}'
        )
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), SeoRecommendations
        )
        audit = SeoAudit(
            page=page,
            target_keyword=keyword,
            checks=checks,
            score=score,
            recommendations=list(result.value.recommendations),
        )
        artifact = Artifact.from_model(
            SEO_AUDIT_KIND,
            audit,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        return AgentOutput(
            artifact=artifact, notes=(f"{len(failed)} of {len(checks)} checks failed",)
        )
