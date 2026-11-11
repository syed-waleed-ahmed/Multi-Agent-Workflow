"""Output contracts of the agent library, wave 2 (Session 8, docs/AGENT_CATALOG.md).

The same rules as wave 1 (ADR-011, ADR-026):

* **Numbers are computed by code.** That covers ICE scores and ranks, experiment
  sample sizes, CTR, CVR, CPC, CPA and ROAS, SEO and CRO check scores, word counts
  and calendar weeks. A model never states a metric it could get wrong.
* **Nothing is invented.**
  - Testimonials and statistics on a landing page must be bracketed placeholders.
  - Keyword volumes come only from a user-supplied CSV.
  - Performance metrics come only from user data.
  - A model's safety flag is kept only if it quotes the content.
* **Limits are contract rules.** A violation is an exact error that the repair
  loop shows to the model.
"""

from __future__ import annotations

import csv
import io
import math
import re
from statistics import NormalDist
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationInfo, model_validator

from ..domain.provenance import Basis, Claim
from .fields import Long, Short, Text, cell, clip, context_of

FUNNEL_KIND = "strategy.funnel@1"
GROWTH_KIND = "strategy.growth@1"
EXPERIMENTS_KIND = "analytics.experiment_plan@1"
PERFORMANCE_KIND = "analytics.performance@1"
CRO_KIND = "analytics.cro@1"
LANDING_KIND = "content.landing_page@1"
PRODUCT_COPY_KIND = "content.product_descriptions@1"
REPURPOSED_KIND = "content.repurposed@1"
ARTICLE_KIND = "content.article@1"
KEYWORDS_KIND = "seo.keywords@1"
SEO_PLAN_KIND = "seo.content_plan@1"
SEO_AUDIT_KIND = "seo.audit@1"
SAFETY_KIND = "quality.safety@1"


def _limited(limit: int) -> Any:
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=limit)]


def _words(text: str) -> int:
    return len(re.findall(r"[\w'-]+", text))


Intent = Literal["informational", "commercial", "transactional", "navigational"]


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #
class FunnelStage(BaseModel):
    name: Short
    goal: Text
    content: list[Short] = Field(min_length=1, max_length=5)
    conversion_point: Short
    metric: Short


class LeadMagnet(BaseModel):
    title: Short
    format: Short
    promise: Text
    stage: Short


class FunnelPlan(BaseModel):
    stages: list[FunnelStage] = Field(min_length=3, max_length=6)
    lead_magnets: list[LeadMagnet] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _magnets_name_real_stages(self) -> FunnelPlan:
        names = {s.name.casefold() for s in self.stages}
        for magnet in self.lead_magnets:
            if magnet.stage.casefold() not in names:
                known = ", ".join(s.name for s in self.stages)
                raise ValueError(
                    f"lead magnet {magnet.title!r} names stage {magnet.stage!r}, which is not "
                    f"one of the funnel's stages ({known})"
                )
        return self

    def digest(self) -> str:
        stages = " > ".join(s.name for s in self.stages)
        return f"Funnel: {stages}; {len(self.lead_magnets)} lead magnet(s)"

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {cell(s.name)} | {cell(s.goal)} | {cell('; '.join(s.content))} | "
            f"{cell(s.conversion_point)} | {cell(s.metric)} |"
            for s in self.stages
        )
        magnets = "\n".join(
            f"- **{m.title}** ({m.format}, {m.stage}): {m.promise}" for m in self.lead_magnets
        )
        return (
            "| Stage | Goal | Content | Conversion point | Metric |\n"
            "| --- | --- | --- | --- | --- |\n" + rows + f"\n\n**Lead magnets:**\n{magnets}"
        )


class GrowthIdea(BaseModel):
    name: Short
    hypothesis: Text
    channel: Short
    impact: int = Field(ge=1, le=10)
    confidence: int = Field(ge=1, le=10)
    ease: int = Field(ge=1, le=10)
    #: Computed by code: the mean of impact, confidence and ease.
    ice: float = 0.0


class GrowthBacklog(BaseModel):
    """Experiment ideas ranked by ICE. The model rates; code scores and ranks."""

    ideas: list[GrowthIdea] = Field(min_length=3, max_length=10)

    @model_validator(mode="after")
    def _score_and_rank(self) -> GrowthBacklog:
        for idea in self.ideas:
            idea.ice = round((idea.impact + idea.confidence + idea.ease) / 3, 2)
        self.ideas.sort(key=lambda i: (-i.ice, i.name.casefold()))
        return self

    def digest(self) -> str:
        top = "; ".join(f"{i.name} (ICE {i.ice:.1f})" for i in self.ideas[:3])
        return f"{len(self.ideas)} growth ideas; top: {top}"

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {n} | {cell(i.name)} | {cell(i.channel)} | {i.impact} | {i.confidence} | "
            f"{i.ease} | {i.ice:.2f} |"
            for n, i in enumerate(self.ideas, start=1)
        )
        hypotheses = "\n".join(f"- **{i.name}:** {i.hypothesis}" for i in self.ideas)
        return (
            "| Rank | Idea | Channel | Impact | Confidence | Ease | ICE |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n" + rows + f"\n\n{hypotheses}"
        )


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
ALPHA = 0.05
POWER = 0.8


def sample_size(baseline: float, relative_lift: float) -> int:
    """Visitors per variant to detect ``relative_lift`` over ``baseline`` (two-sided,
    two-proportion z-test, alpha 0.05, power 0.8)."""
    p1 = baseline
    p2 = min(p1 * (1 + relative_lift), 0.999)
    z_alpha = NormalDist().inv_cdf(1 - ALPHA / 2)
    z_beta = NormalDist().inv_cdf(POWER)
    pooled = (p1 + p2) / 2
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return math.ceil(numerator / (p2 - p1) ** 2)


class Experiment(BaseModel):
    name: Short
    hypothesis: Text
    control: Text
    variant: Text
    primary_metric: Short
    #: The current conversion rate of the primary metric (0-1). An assumption
    #: unless it comes from the user's performance data.
    baseline_rate: float = Field(gt=0.0, lt=1.0)
    #: The smallest relative lift worth detecting, e.g. 0.1 for +10%.
    minimum_detectable_effect: float = Field(gt=0.0, le=1.0)
    sample_size_per_variant: int = 0
    #: Set by code when the baseline came from the user's performance data.
    baseline_measured: bool = False


class ExperimentPlan(BaseModel):
    experiments: list[Experiment] = Field(min_length=1, max_length=4)
    #: "measured" only when baselines came from the user's performance data.
    baseline_source: Literal["assumed", "measured"] = "assumed"

    @model_validator(mode="after")
    def _size(self, info: ValidationInfo) -> ExperimentPlan:
        # The agent always passes "measured_rates" (empty without data), so on that path
        # code decides every flag: a model can never mark its own guess as measured. With
        # the user's data, click and conversion baselines are set from the measured rates.
        # Re-reading a stored artifact (no context) keeps the flags code set before.
        context = context_of(info)
        if "measured_rates" in context:
            measured: dict[str, float] = context["measured_rates"] or {}
            for experiment in self.experiments:
                metric = experiment.primary_metric.casefold()
                rate = measured.get("ctr") if "click" in metric else None
                rate = measured.get("cvr") if rate is None and "conver" in metric else rate
                experiment.baseline_measured = rate is not None and 0 < rate < 1
                if experiment.baseline_measured and rate is not None:
                    experiment.baseline_rate = rate
        all_measured = all(e.baseline_measured for e in self.experiments)
        self.baseline_source = "measured" if all_measured else "assumed"
        for experiment in self.experiments:
            experiment.sample_size_per_variant = sample_size(
                experiment.baseline_rate, experiment.minimum_detectable_effect
            )
        return self

    def claims(self) -> tuple[Claim, ...]:
        return tuple(
            Claim(
                statement=(
                    f"Baseline {e.primary_metric} for '{e.name}' assumed at "
                    f"{e.baseline_rate:.1%}; replace it with your measured rate."
                ),
                basis=Basis.MODEL_ASSUMPTION,
            )
            for e in self.experiments
            if not e.baseline_measured
        )

    def digest(self) -> str:
        return "; ".join(
            f"{e.name}: {e.primary_metric}, {e.sample_size_per_variant:,} per variant"
            for e in self.experiments
        )

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {cell(e.name)} | {cell(e.primary_metric)} | {e.baseline_rate:.1%} | "
            f"+{e.minimum_detectable_effect:.0%} | {e.sample_size_per_variant:,} |"
            for e in self.experiments
        )
        details = "\n".join(
            f"- **{e.name}:** {e.hypothesis} Control: {e.control} Variant: {e.variant}"
            for e in self.experiments
        )
        note = (
            "Baselines are assumptions; replace them with measured rates."
            if self.baseline_source == "assumed"
            else "Baselines come from your performance data."
        )
        return (
            "| Experiment | Primary metric | Baseline | Detect | Sample per variant |\n"
            "| --- | --- | --- | --- | --- |\n"
            + rows
            + f"\n\nSample sizes: two-sided test, alpha {ALPHA}, power {POWER:.0%}. {note}\n\n"
            + details
        )


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


class ChannelMetrics(BaseModel):
    name: str
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(ge=0)
    spend: float = Field(ge=0)
    revenue: float | None = Field(default=None, ge=0)
    ctr: float | None = None
    cvr: float | None = None
    cpc: float | None = None
    cpa: float | None = None
    roas: float | None = None

    @model_validator(mode="after")
    def _compute(self) -> ChannelMetrics:
        self.ctr = _ratio(self.clicks, self.impressions)
        self.cvr = _ratio(self.conversions, self.clicks)
        self.cpc = _ratio(self.spend, self.clicks)
        self.cpa = _ratio(self.spend, self.conversions)
        self.roas = _ratio(self.revenue, self.spend) if self.revenue is not None else None
        return self


_COLUMNS = {
    "name": ("channel", "campaign", "name", "source"),
    "impressions": ("impressions", "impr"),
    "clicks": ("clicks",),
    "conversions": ("conversions", "purchases", "leads", "signups"),
    "spend": ("spend", "cost", "amount spent"),
    "revenue": ("revenue", "sales", "conversion value"),
}


def parse_performance_csv(text: str) -> list[ChannelMetrics]:
    """Rows of channel metrics from a CSV with a header row. Revenue is optional.

    Raises:
        ValueError: with the missing columns, or the first malformed row.
    """
    reader = csv.DictReader(io.StringIO(text.strip()))
    headers = {h.strip().casefold(): h for h in reader.fieldnames or []}
    found = {
        field: next((headers[a] for a in aliases if a in headers), None)
        for field, aliases in _COLUMNS.items()
    }
    missing = [f for f in ("name", "impressions", "clicks", "conversions", "spend") if not found[f]]
    if missing:
        raise ValueError(f"performance CSV is missing column(s): {', '.join(missing)}")
    columns = {field: header for field, header in found.items() if header}
    rows = []
    for number, row in enumerate(reader, start=2):
        try:

            def num(field: str, row: dict[str, str] = row) -> float:
                return float(str(row[columns[field]]).replace(",", "").replace("$", "").strip())

            revenue = columns.get("revenue")
            rows.append(
                ChannelMetrics(
                    name=str(row[columns["name"]]).strip() or f"row {number}",
                    impressions=int(num("impressions")),
                    clicks=int(num("clicks")),
                    conversions=int(num("conversions")),
                    spend=num("spend"),
                    revenue=num("revenue") if revenue and str(row[revenue]).strip() else None,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"performance CSV row {number} is not valid: {exc}") from exc
    if not rows:
        raise ValueError("performance CSV has no data rows")
    return rows


def totals(rows: list[ChannelMetrics]) -> ChannelMetrics:
    has_revenue = all(r.revenue is not None for r in rows)
    return ChannelMetrics(
        name="Total",
        impressions=sum(r.impressions for r in rows),
        clicks=sum(r.clicks for r in rows),
        conversions=sum(r.conversions for r in rows),
        spend=round(sum(r.spend for r in rows), 2),
        revenue=round(sum(r.revenue or 0 for r in rows), 2) if has_revenue else None,
    )


class PerformanceNarrative(BaseModel):
    """What the model writes about the computed metrics (it never computes them)."""

    summary: Long
    insights: list[Text] = Field(min_length=2, max_length=5)
    recommendations: list[Text] = Field(min_length=2, max_length=5)


class PerformanceReport(BaseModel):
    source: str
    rows: list[ChannelMetrics]
    total: ChannelMetrics
    summary: str
    insights: list[str]
    recommendations: list[str]

    def digest(self) -> str:
        t = self.total
        parts = [f"{len(self.rows)} channel(s)", f"CTR {_pct(t.ctr)}", f"CVR {_pct(t.cvr)}"]
        parts += [f"CPA {_money(t.cpa)}", f"ROAS {_x(t.roas)}"]
        return "Performance: " + ", ".join(parts)

    def to_markdown(self) -> str:
        def row(m: ChannelMetrics) -> str:
            return (
                f"| {cell(m.name)} | {m.impressions:,} | {m.clicks:,} | {m.conversions:,} | "
                f"{_money(m.spend)} | {_pct(m.ctr)} | {_pct(m.cvr)} | {_money(m.cpc)} | "
                f"{_money(m.cpa)} | {_x(m.roas)} |"
            )

        table = "\n".join(row(m) for m in [*self.rows, self.total])
        insights = "\n".join(f"- {i}" for i in self.insights)
        actions = "\n".join(f"- {r}" for r in self.recommendations)
        return (
            f"Source: {self.source}. Metrics are computed by code from your data.\n\n"
            "| Channel | Impressions | Clicks | Conversions | Spend | CTR | CVR | CPC | CPA "
            "| ROAS |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            f"{table}\n\n{self.summary}\n\n**Insights:**\n{insights}\n\n"
            f"**Recommendations:**\n{actions}"
        )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def _x(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


class CroCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class CroRecommendation(BaseModel):
    change: Text
    reason: Text
    impact: Literal["high", "medium", "low"]


class CroRecommendations(BaseModel):
    recommendations: list[CroRecommendation] = Field(min_length=2, max_length=6)


class CroAdvice(BaseModel):
    checks: list[CroCheck]
    score: float = Field(ge=0.0, le=1.0)
    recommendations: list[CroRecommendation]

    @model_validator(mode="after")
    def _order(self) -> CroAdvice:
        rank = {"high": 0, "medium": 1, "low": 2}
        self.recommendations.sort(key=lambda r: rank[r.impact])
        return self

    def digest(self) -> str:
        failed = [c.name for c in self.checks if not c.passed]
        return f"CRO checklist {self.score:.0%}; failed: {', '.join(failed) or 'none'}"

    def to_markdown(self) -> str:
        checks = "\n".join(
            f"- [{'x' if c.passed else ' '}] {c.name}: {c.detail}" for c in self.checks
        )
        recs = "\n".join(f"- **{r.impact}:** {r.change} ({r.reason})" for r in self.recommendations)
        return f"**Checklist ({self.score:.0%}):**\n{checks}\n\n**Recommendations:**\n{recs}"


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
class Benefit(BaseModel):
    title: Short
    text: Text


_PLACEHOLDER_RE = re.compile(r"^\[.+\]$", re.DOTALL)


class ProofPoint(BaseModel):
    kind: Literal["testimonial", "statistic", "guarantee", "feature"]
    text: Text

    @model_validator(mode="after")
    def _no_invented_proof(self) -> ProofPoint:
        if self.kind in ("testimonial", "statistic") and not _PLACEHOLDER_RE.match(self.text):
            raise ValueError(
                f"a {self.kind} must be a bracketed placeholder such as "
                f"'[Customer {self.kind} to be supplied]'; never invent one"
            )
        return self


class FaqItem(BaseModel):
    question: Short
    answer: Text


class LandingPage(BaseModel):
    headline: _limited(70)  # type: ignore[valid-type]
    subheadline: Text
    benefits: list[Benefit] = Field(min_length=3, max_length=6)
    proof: list[ProofPoint] = Field(min_length=1, max_length=5)
    faq: list[FaqItem] = Field(min_length=3, max_length=6)
    cta_text: Short
    cta_button: _limited(30)  # type: ignore[valid-type]

    def text(self) -> str:
        parts = [self.headline, self.subheadline, self.cta_text, self.cta_button]
        parts += [f"{b.title}. {b.text}" for b in self.benefits]
        parts += [p.text for p in self.proof] + [f"{f.question} {f.answer}" for f in self.faq]
        return "\n".join(parts)

    def digest(self) -> str:
        return (
            f"Landing page: {self.headline!r}; {len(self.benefits)} benefits, "
            f"{len(self.faq)} FAQs; CTA {self.cta_button!r}"
        )

    def to_markdown(self) -> str:
        benefits = "\n".join(f"- **{b.title}:** {b.text}" for b in self.benefits)
        proof = "\n".join(f"- ({p.kind}) {p.text}" for p in self.proof)
        faq = "\n".join(f"**{f.question}**\n\n{f.answer}\n" for f in self.faq)
        return (
            f"### {self.headline}\n\n{self.subheadline}\n\n**[{self.cta_button}]** "
            f"{self.cta_text}\n\n**Benefits:**\n{benefits}\n\n**Proof:**\n{proof}\n\n"
            f"**FAQ:**\n\n{faq}"
        )


class ProductDescriptions(BaseModel):
    title: _limited(150)  # type: ignore[valid-type]
    short_description: _limited(160)  # type: ignore[valid-type]
    long_description: Long
    bullets: list[_limited(200)] = Field(min_length=3, max_length=7)  # type: ignore[valid-type]
    seo_title: _limited(60)  # type: ignore[valid-type]
    meta_description: _limited(160)  # type: ignore[valid-type]

    def text(self) -> str:
        return "\n".join(
            [
                self.title,
                self.short_description,
                self.long_description,
                *self.bullets,
                self.seo_title,
                self.meta_description,
            ]
        )

    def digest(self) -> str:
        return f"Product copy: {self.title!r}; {len(self.bullets)} bullets"

    def to_markdown(self) -> str:
        bullets = "\n".join(f"- {b}" for b in self.bullets)
        return (
            f"**{self.title}**\n\n{self.short_description}\n\n{self.long_description}\n\n"
            f"{bullets}\n\n**SEO title:** {self.seo_title}\n\n"
            f"**Meta description:** {self.meta_description}"
        )


#: Character limits per repurposed format (an X thread's limit applies per post).
REPURPOSE_LIMITS: dict[str, int] = {
    "x_thread": 280,
    "linkedin_post": 3000,
    "instagram_caption": 2200,
    "newsletter_blurb": 1200,
    "video_script": 2500,
    "faq": 2500,
}
RepurposeFormat = Literal[
    "x_thread", "linkedin_post", "instagram_caption", "newsletter_blurb", "video_script", "faq"
]


class RepurposedPiece(BaseModel):
    format: RepurposeFormat
    content: Long

    @model_validator(mode="after")
    def _fits(self) -> RepurposedPiece:
        limit = REPURPOSE_LIMITS[self.format]
        if self.format == "x_thread":
            posts = [p.strip() for p in self.content.split("\n\n") if p.strip()]
            for number, post in enumerate(posts, start=1):
                if len(post) > limit:
                    raise ValueError(
                        f"x_thread post {number} is {len(post)} characters; X allows {limit}. "
                        "Separate posts with a blank line."
                    )
        elif len(self.content) > limit:
            raise ValueError(f"{self.format} is {len(self.content)} characters; limit {limit}")
        return self


class RepurposedContent(BaseModel):
    source: Short
    pieces: list[RepurposedPiece] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def _distinct_formats(self) -> RepurposedContent:
        formats = [p.format for p in self.pieces]
        if len(set(formats)) != len(formats):
            raise ValueError("each format may appear only once")
        return self

    def text(self) -> str:
        return "\n".join(p.content for p in self.pieces)

    def digest(self) -> str:
        return f"Repurposed {self.source!r} into: {', '.join(p.format for p in self.pieces)}"

    def to_markdown(self) -> str:
        return "\n\n".join(
            f"**{p.format.replace('_', ' ').title()}:**\n\n{p.content}" for p in self.pieces
        )


class OutlineSection(BaseModel):
    heading: Short
    points: list[Short] = Field(min_length=1, max_length=5)


class ArticleOutline(BaseModel):
    title: _limited(70)  # type: ignore[valid-type]
    meta_description: _limited(160)  # type: ignore[valid-type]
    sections: list[OutlineSection] = Field(min_length=3, max_length=6)


class ArticleSection(BaseModel):
    heading: str
    body: str = Field(min_length=1)


class Article(BaseModel):
    title: str
    meta_description: str
    target_keyword: str | None = None
    sections: list[ArticleSection] = Field(min_length=1)
    word_count: int = 0

    @model_validator(mode="after")
    def _count(self) -> Article:
        self.word_count = _words(self.markdown_body())
        return self

    def markdown_body(self) -> str:
        return "\n\n".join(f"## {s.heading}\n\n{s.body}" for s in self.sections)

    def text(self) -> str:
        return f"{self.title}\n{self.meta_description}\n{self.markdown_body()}"

    def digest(self) -> str:
        return f"Article {self.title!r}: {len(self.sections)} sections, {self.word_count} words"

    def to_markdown(self) -> str:
        return f"# {self.title}\n\n_{self.meta_description}_\n\n{self.markdown_body()}"


# --------------------------------------------------------------------------- #
# SEO
# --------------------------------------------------------------------------- #
class KeywordCluster(BaseModel):
    name: Short
    intent: Intent
    keywords: list[_limited(80)] = Field(min_length=2, max_length=10)  # type: ignore[valid-type]
    #: Monthly volumes, filled by code from the user's CSV only.
    volumes: dict[str, int] = Field(default_factory=dict)


class KeywordPlan(BaseModel):
    seed_keywords: list[_limited(80)] = Field(min_length=1, max_length=8)  # type: ignore[valid-type]
    clusters: list[KeywordCluster] = Field(min_length=2, max_length=8)
    volume_source: str = "none: no keyword data was supplied"

    @model_validator(mode="after")
    def _volumes_from_user_data_only(self, info: ValidationInfo) -> KeywordPlan:
        # The agent always passes "keyword_volumes" (empty without a CSV), so whatever the
        # model wrote, volumes come only from the user's data. Re-reading a stored
        # artifact (no context) keeps what code filled in before.
        context = context_of(info)
        if "keyword_volumes" in context:
            supplied: dict[str, int] = context["keyword_volumes"] or {}
            for cluster in self.clusters:
                cluster.volumes = {
                    k: supplied[k.casefold()] for k in cluster.keywords if k.casefold() in supplied
                }
            source = context.get("volume_source")
            self.volume_source = (
                str(source) if supplied and source else "none: no keyword data was supplied"
            )
        return self

    @property
    def primary_keyword(self) -> str:
        return str(self.clusters[0].keywords[0])

    def digest(self) -> str:
        clusters = "; ".join(f"{c.name} ({c.intent})" for c in self.clusters)
        return f"Keywords: {clusters}. Volumes: {self.volume_source}"

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {cell(c.name)} | {c.intent} | "
            + cell(
                ", ".join(f"{k} ({c.volumes[k]:,})" if k in c.volumes else k for k in c.keywords)
            )
            + " |"
            for c in self.clusters
        )
        return (
            f"**Seed keywords:** {', '.join(self.seed_keywords)}\n\n"
            "| Cluster | Intent | Keywords (monthly volume where supplied) |\n| --- | --- | --- |\n"
            f"{rows}\n\nVolume data: {self.volume_source}."
        )


def keyword_volumes(text: str) -> dict[str, int]:
    """Keyword volumes from a user CSV with ``keyword`` and ``volume`` columns."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    headers = {h.strip().casefold(): h for h in reader.fieldnames or []}
    key = headers.get("keyword") or headers.get("query") or headers.get("term")
    volume = headers.get("volume") or headers.get("search volume") or headers.get("monthly volume")
    if not key or not volume:
        return {}
    volumes = {}
    for row in reader:
        word = str(row[key]).strip().casefold()
        raw = str(row[volume]).replace(",", "").strip()
        if word and raw.isdigit():
            volumes[word] = int(raw)
    return volumes


class ContentPiece(BaseModel):
    title: _limited(70)  # type: ignore[valid-type]
    target_keyword: Short
    intent: Intent
    outline: list[Short] = Field(min_length=3, max_length=8)
    internal_links: list[Short] = Field(default_factory=list, max_length=5)
    #: Computed by code: one piece a week, in the given order.
    week: int = 0


class SeoContentPlan(BaseModel):
    pieces: list[ContentPiece] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def _schedule(self) -> SeoContentPlan:
        for week, piece in enumerate(self.pieces, start=1):
            piece.week = week
        return self

    def digest(self) -> str:
        return f"SEO plan: {len(self.pieces)} pieces over {len(self.pieces)} weeks; first: " + (
            f"{self.pieces[0].title!r}"
        )

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {p.week} | {cell(p.title)} | {cell(p.target_keyword)} | {p.intent} | "
            f"{cell('; '.join(p.outline))} |"
            for p in self.pieces
        )
        return (
            "| Week | Title | Keyword | Intent | Outline |\n| --- | --- | --- | --- | --- |\n"
            + rows
        )


class SeoCheck(BaseModel):
    name: str
    passed: bool
    detail: str


def seo_checks(title: str, meta: str, body: str, keyword: str | None) -> list[SeoCheck]:
    """Deterministic on-page checks for a page given as a title, a meta description and Markdown."""
    words = _words(body)
    headings = re.findall(r"^#{2,3}\s+(.+)$", body, re.MULTILINE)
    checks = [
        SeoCheck(
            name="title_length",
            passed=30 <= len(title) <= 60,
            detail=f"{len(title)} characters (aim for 30-60)",
        ),
        SeoCheck(
            name="meta_description",
            passed=70 <= len(meta) <= 160,
            detail=f"{len(meta)} characters (aim for 70-160)" if meta else "missing",
        ),
        SeoCheck(
            name="subheadings",
            passed=len(headings) >= 2,
            detail=f"{len(headings)} subheading(s) (aim for 2 or more)",
        ),
        SeoCheck(name="word_count", passed=words >= 600, detail=f"{words} words (aim for 600+)"),
    ]
    if keyword:
        needle = keyword.casefold()
        paragraphs = [p for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith("#")]
        first = paragraphs[0].casefold() if paragraphs else ""
        hits = body.casefold().count(needle)
        in_title = needle in title.casefold()
        density = hits * len(keyword.split()) / words if words else 0.0
        checks += [
            SeoCheck(
                name="keyword_in_title",
                passed=in_title,
                detail=f"{keyword!r} {'is' if in_title else 'is not'} in the title",
            ),
            SeoCheck(
                name="keyword_early",
                passed=needle in first,
                detail="in the first paragraph"
                if needle in first
                else "not in the first paragraph",
            ),
            SeoCheck(
                name="keyword_in_subheading",
                passed=any(needle in h.casefold() for h in headings),
                detail="in a subheading"
                if any(needle in h.casefold() for h in headings)
                else "in no subheading",
            ),
            SeoCheck(
                name="keyword_density",
                passed=0.005 <= density <= 0.025,
                detail=f"{density:.1%} (aim for 0.5-2.5%)",
            ),
        ]
    return checks


class SeoRecommendations(BaseModel):
    recommendations: list[Text] = Field(min_length=2, max_length=6)


class SeoAudit(BaseModel):
    page: str
    target_keyword: str | None
    checks: list[SeoCheck]
    score: float = Field(ge=0.0, le=1.0)
    recommendations: list[str]

    def digest(self) -> str:
        failed = [c.name for c in self.checks if not c.passed]
        return (
            f"SEO audit of {self.page!r}: {self.score:.0%}; failed: {', '.join(failed) or 'none'}"
        )

    def to_markdown(self) -> str:
        checks = "\n".join(
            f"- [{'x' if c.passed else ' '}] {c.name}: {c.detail}" for c in self.checks
        )
        recs = "\n".join(f"- {r}" for r in self.recommendations)
        return f"**On-page checks ({self.score:.0%}):**\n{checks}\n\n**Recommendations:**\n{recs}"


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #
SafetyCategory = Literal["health", "finance", "environmental", "superlative", "targeting", "other"]

#: Phrases that need substantiation or legal review before publishing. Matched by code.
SAFETY_RULES: dict[str, re.Pattern[str]] = {
    "health": re.compile(
        r"\b(cures?|heals?|treats? (?:\w+ )?(?:disease|illness|anxiety|pain)|clinically proven|"
        r"doctor[- ]recommended|detox(?:es|ifies)?|boosts? (?:your )?immun\w*)\b",
        re.IGNORECASE,
    ),
    "finance": re.compile(
        r"\b(guaranteed (?:returns?|income|profits?)|risk[- ]free|get rich|double your money|"
        r"no[- ]risk investment)\b",
        re.IGNORECASE,
    ),
    "environmental": re.compile(
        r"\b(eco[- ]friendly|carbon[- ](?:neutral|negative)|climate[- ]positive|"
        r"100% (?:sustainable|recyclable|natural|green)|biodegradable|zero[- ]waste|"
        r"planet[- ]friendly|environmentally safe)\b",
        re.IGNORECASE,
    ),
    "superlative": re.compile(
        r"(#1\b|\bnumber one\b|\bworld'?s (?:first|best)\b|\bthe best\b|\bunbeatable\b|"
        r"\bguaranteed\b)",
        re.IGNORECASE,
    ),
}
SAFETY_DISCLAIMER = (
    "Flags are prompts for review, not legal advice. Check regulated claims with a qualified "
    "reviewer before publishing."
)


class ModelSafetyFlag(BaseModel):
    category: SafetyCategory
    deliverable: Short
    quote: Text
    note: Text


class ModelSafetyFlags(BaseModel):
    flags: list[ModelSafetyFlag] = Field(default_factory=list, max_length=10)


class SafetyFlag(BaseModel):
    category: SafetyCategory
    deliverable: str
    quote: str
    source: Literal["rule", "model"]
    note: str


class SafetyReview(BaseModel):
    checked: list[str]
    flags: list[SafetyFlag]
    dropped_model_flags: int = 0
    passed: bool = True

    @model_validator(mode="after")
    def _verdict(self) -> SafetyReview:
        self.passed = not self.flags
        return self

    def digest(self) -> str:
        cats = sorted({f.category for f in self.flags})
        return (
            f"Safety review: {len(self.flags)} flag(s)"
            + (f" ({', '.join(cats)})" if cats else "")
            + f" across {', '.join(self.checked)}"
        )

    def to_markdown(self) -> str:
        if not self.flags:
            body = "_No risky claims were flagged._"
        else:
            body = "| Category | Deliverable | Quote | Source | Note |\n"
            body += "| --- | --- | --- | --- | --- |\n"
            body += "\n".join(
                f"| {f.category} | {cell(f.deliverable)} | {cell(clip(f.quote, 120))} | "
                f"{f.source} | {cell(f.note)} |"
                for f in self.flags
            )
        return f"{body}\n\n{SAFETY_DISCLAIMER}"


def rule_flags(deliverable: str, text: str) -> list[SafetyFlag]:
    """Code-detected risky phrases in ``text``, one flag per distinct phrase and category."""
    flags = []
    for category, pattern in SAFETY_RULES.items():
        seen: set[str] = set()
        for match in pattern.finditer(text):
            phrase = match.group(0)
            if phrase.casefold() in seen:
                continue
            seen.add(phrase.casefold())
            flags.append(
                SafetyFlag(
                    category=category,
                    deliverable=deliverable,
                    quote=phrase,
                    source="rule",
                    note=f"'{phrase}' is a {category} claim that needs substantiation or review.",
                )
            )
    return flags


def quoted_in(quote: str, text: str) -> bool:
    """Whether ``quote`` appears in ``text`` (case- and whitespace-insensitive)."""

    def norm(value: str) -> str:
        return " ".join(value.casefold().split())

    return bool(quote.strip()) and norm(quote) in norm(text)


WAVE2_MODELS: dict[str, type[BaseModel]] = {
    FUNNEL_KIND: FunnelPlan,
    GROWTH_KIND: GrowthBacklog,
    EXPERIMENTS_KIND: ExperimentPlan,
    PERFORMANCE_KIND: PerformanceReport,
    CRO_KIND: CroAdvice,
    LANDING_KIND: LandingPage,
    PRODUCT_COPY_KIND: ProductDescriptions,
    REPURPOSED_KIND: RepurposedContent,
    ARTICLE_KIND: Article,
    KEYWORDS_KIND: KeywordPlan,
    SEO_PLAN_KIND: SeoContentPlan,
    SEO_AUDIT_KIND: SeoAudit,
    SAFETY_KIND: SafetyReview,
}
