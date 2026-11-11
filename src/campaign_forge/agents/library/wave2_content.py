"""Content agents, wave 2: landing page, product descriptions, repurposing, long-form articles.

* ``landing_page`` may use testimonials and statistics only as bracketed
  placeholders. The contract rejects invented ones.
* ``product_description`` limits are contract rules (the SEO title stays within 60
  characters, and so on).
* ``repurposer`` works from the campaign's core copy or from the user's own text.
* ``blog_writer`` writes outline-first, then one section per call, so each call
  fits a free tier's per-request output cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.sources import SOURCES_KIND, SourceBundle
from ...exceptions import AgentError, OutputParsingError
from ...providers.base import CompletionRequest, CompletionResponse
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import AUDIENCE_KIND, CORE_COPY_KIND, POSITIONING_KIND
from ..schemas_wave2 import (
    ARTICLE_KIND,
    KEYWORDS_KIND,
    LANDING_KIND,
    PRODUCT_COPY_KIND,
    REPURPOSE_LIMITS,
    REPURPOSED_KIND,
    SEO_PLAN_KIND,
    Article,
    ArticleOutline,
    ArticleSection,
    KeywordPlan,
    LandingPage,
    ProductDescriptions,
    RepurposedContent,
    SeoContentPlan,
)
from .common import (
    BRIEF,
    JSON_ONLY,
    OPTIONAL_BRAND,
    AgentCategory,
    StructuredAgent,
    brief_of,
    context,
    framed_brand,
    framed_brief,
    make_spec,
    produced_by,
    total_usage,
)

COPY = InputSpec(name="copy", kind=CORE_COPY_KIND)
OPTIONAL_COPY = InputSpec(name="copy", kind=CORE_COPY_KIND, required=False)
OPTIONAL_POSITIONING = InputSpec(name="positioning", kind=POSITIONING_KIND, required=False)
OPTIONAL_AUDIENCE = InputSpec(name="audience", kind=AUDIENCE_KIND, required=False)
OPTIONAL_SOURCES = InputSpec(name="sources", kind=SOURCES_KIND, required=False)
OPTIONAL_KEYWORDS = InputSpec(name="keywords", kind=KEYWORDS_KIND, required=False)
OPTIONAL_SEO_PLAN = InputSpec(name="plan", kind=SEO_PLAN_KIND, required=False)
_MAX_SOURCE_CHARS = 6000
_SECTION_WORDS = (150, 260)


def framed_sources(inputs: Mapping[str, Artifact], limit: int = _MAX_SOURCE_CHARS) -> str:
    """The user's own documents, framed as data, or "" when there are none."""
    if "sources" not in inputs:
        return ""
    bundle = inputs["sources"].as_model(SourceBundle)
    text = "\n\n".join(f"[{d.name}]\n{d.content}" for d in bundle.documents)
    if not text.strip():
        return ""
    return f"Your source material:\n{frame_untrusted(text[:limit], label='user sources')}\n\n"


class LandingPageAgent(StructuredAgent):
    spec = make_spec(
        "landing_page",
        "Landing page",
        "Landing-page copy: hero, benefits, proof (placeholders, never invented), FAQ, CTA.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, COPY, OPTIONAL_POSITIONING, OPTIONAL_AUDIENCE, OPTIONAL_BRAND),
        output_kind=LANDING_KIND,
        capabilities=("landing_pages", "conversion_copy"),
        rubric=("on_brief", "clarity", "persuasiveness", "tone_match"),
        max_tokens=1400,
        reviewable=True,
    )
    output_model: ClassVar[type[LandingPage]] = LandingPage
    role = (
        "You are a conversion copywriter. You write landing pages with one clear promise, "
        "concrete benefits and a single call to action, and you never invent proof."
    )
    shape = (
        '{"headline": "...", "subheadline": "...", "benefits": [{"title": "...", "text": "..."}], '
        '"proof": [{"kind": "testimonial", "text": "[Customer quote to be supplied]"}], '
        '"faq": [{"question": "...", "answer": "..."}], "cta_text": "...", "cta_button": "..."}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        brief = brief_of(inputs)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{context(inputs, 'copy', 'positioning', 'audience')}\n\n"
            f"Task: write the landing page for {brief.product_name} in a {brief.tone} tone: a "
            "headline of at most 70 characters, a subheadline, 3-5 benefits, 1-3 proof points, "
            "3-5 FAQs, a CTA sentence and a button label of at most 30 characters. Proof of kind "
            "'testimonial' or 'statistic' must be a bracketed placeholder such as "
            "'[Customer quote to be supplied]'; use 'guarantee' or 'feature' only for facts the "
            "brief states."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class ProductDescriptionAgent(StructuredAgent):
    spec = make_spec(
        "product_description",
        "Product description",
        "Product title, short and long descriptions, bullets, SEO title and meta description "
        "within marketplace limits.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, OPTIONAL_COPY, OPTIONAL_POSITIONING, OPTIONAL_BRAND),
        output_kind=PRODUCT_COPY_KIND,
        capabilities=("product_descriptions", "ecommerce"),
        rubric=("on_brief", "clarity", "persuasiveness", "platform_fit"),
        max_tokens=1000,
        reviewable=True,
    )
    output_model: ClassVar[type[ProductDescriptions]] = ProductDescriptions
    role = (
        "You are an e-commerce copywriter. You describe products accurately and persuasively "
        "within marketplace limits, using only facts from the brief."
    )
    shape = (
        '{"title": "...", "short_description": "...", "long_description": "...", '
        '"bullets": ["..."], "seo_title": "...", "meta_description": "..."}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{context(inputs, 'copy', 'positioning')}\n\n"
            "Task: write product-page copy: a title (max 150 characters), a short description "
            "(max 160), a long description (one or two paragraphs), 3-6 bullets (max 200 each), "
            "an SEO title (max 60) and a meta description (max 160). State only facts the brief "
            "gives."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class RepurposerAgent(StructuredAgent):
    spec = make_spec(
        "repurposer",
        "Repurposer",
        "Turns the campaign's core copy or your own text into other formats within each "
        "platform's limits.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, OPTIONAL_COPY, OPTIONAL_SOURCES, OPTIONAL_BRAND),
        output_kind=REPURPOSED_KIND,
        capabilities=("content_repurposing",),
        rubric=("on_brief", "platform_fit", "variety", "tone_match"),
        max_tokens=1500,
        reviewable=True,
    )
    output_model: ClassVar[type[RepurposedContent]] = RepurposedContent
    role = (
        "You are a content strategist who adapts one piece of content to many channels "
        "without changing its facts."
    )
    shape = (
        '{"source": "...", "pieces": [{"format": "x_thread", "content": "post one\\n\\npost two"}, '
        '{"format": "linkedin_post", "content": "..."}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        formats = ", ".join(REPURPOSE_LIMITS)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{context(inputs, 'copy')}\n\n{framed_sources(inputs)}"
            "Task: repurpose the source material above (your notes take priority over the core "
            f"copy) into 3-5 different formats from: {formats}. Keep every fact unchanged. In an "
            "x_thread, separate posts with a blank line and keep each under 280 characters. Set "
            "'source' to a short description of what you repurposed."
        )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        if "copy" not in inputs and "sources" not in inputs:
            raise AgentError(self.spec.id, "needs the core copy or your own text to repurpose")
        return self.produce(inputs, ctx)

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class BlogWriterAgent:
    """A long-form article: one outline call, then one call per section (free-tier friendly)."""

    spec = make_spec(
        "blog_writer",
        "Blog writer",
        "Long-form article written outline-first, then section by section.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, OPTIONAL_KEYWORDS, OPTIONAL_SEO_PLAN, OPTIONAL_SOURCES, OPTIONAL_BRAND),
        output_kind=ARTICLE_KIND,
        capabilities=("blog_posts", "long_form", "articles"),
        rubric=("on_brief", "clarity", "depth", "structure"),
        max_tokens=700,
        max_calls=10,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        brief = brief_of(inputs)
        keyword = None
        if "keywords" in inputs:
            keyword = inputs["keywords"].as_model(KeywordPlan).primary_keyword
        topic = ""
        if "plan" in inputs:
            first = inputs["plan"].as_model(SeoContentPlan).pieces[0]
            planned = "; ".join(first.outline)
            topic = f"Write the planned piece {first.title!r} (outline: {planned})."
            keyword = first.target_keyword
        material = (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{framed_sources(inputs, 3000)}"
        )
        system = with_untrusted_policy(
            "You are a senior content writer. You write useful, specific articles for real "
            f"readers, not filler. {JSON_ONLY}"
        )
        focus = f" Target keyword: {keyword!r}; use it naturally." if keyword else ""
        outline = ctx.complete_structured(
            self.spec,
            CompletionRequest.from_prompts(
                system,
                f"{material}Task: outline a helpful blog article related to "
                f"{brief.product_name} for {brief.target_audience}. {topic}{focus} Give a title "
                "(max 70 characters), a meta description (max 160) and 3-5 sections, each with "
                'a heading and 1-4 points.\n\nReturn ONLY a JSON object: {"title": "...", '
                '"meta_description": "...", "sections": [{"heading": "...", "points": ["..."]}]}',
            ),
            ArticleOutline,
        )
        plan = outline.value
        responses: list[CompletionResponse] = list(outline.responses)
        sections = []
        writer = with_untrusted_policy(
            "You are a senior content writer. Write plain Markdown paragraphs (no headings, no "
            "JSON). Be specific and only claim what the brief and sources support."
        )
        low, high = _SECTION_WORDS
        for section in plan.sections:
            request = CompletionRequest.from_prompts(
                writer,
                f"{material}Article: {plan.title}\nSection: {section.heading}\nPoints to cover: "
                f"{'; '.join(section.points)}.{focus}\n\nWrite this section only, in {low}-{high} "
                "words.",
            )
            response = ctx.complete(self.spec, request)
            responses.append(response)
            body = response.text.strip()
            if len(body.split()) < 40:
                raise OutputParsingError(
                    self.spec.id, f"section {section.heading!r} came back too short"
                )
            sections.append(ArticleSection(heading=section.heading, body=body))
        article = Article(
            title=plan.title,
            meta_description=plan.meta_description,
            target_keyword=keyword,
            sections=sections,
        )
        artifact = Artifact.from_model(
            ARTICLE_KIND,
            article,
            produced_by=produced_by(self.spec, tuple(responses)),
            usage=total_usage(tuple(responses)),
        )
        return AgentOutput(artifact=artifact, notes=(article.digest(),))
