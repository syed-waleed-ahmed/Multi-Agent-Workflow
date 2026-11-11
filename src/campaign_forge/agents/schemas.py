"""Output contracts of the 2.0 agent library (docs/AGENT_CATALOG.md, wave 1).

Each model is the payload of one artifact kind. Validation rules live on the
models, so a violation becomes a precise error that the structured-output repair
loop shows to the model (for example, "headline is 42 characters; Google Search
allows 30"). Derived numbers are computed here by code, never by the model
(ADR-011): budget shares, calendar weeks, ordering, normalised hashtags.

Every model also offers ``digest()``, a compact text summary used as context for
downstream agents. This keeps prompts small enough for free-tier
tokens-per-minute limits. ``to_markdown()`` renders exact deliverables for the
final report.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..domain.brand import BRAND_KIND, BrandProfile, term_counts
from ..domain.provenance import Basis, Claim
from ..domain.review import REVIEW_KIND, ReviewResult
from ..domain.sources import SOURCES_KIND, SourceBundle
from ..models import MAX_CHANNELS, CampaignBrief
from .fields import Long, Short, Text, cell, clip, context_of
from .schemas_wave2 import WAVE2_MODELS

BRIEF_KIND = "input.brief@1"
AUDIENCE_KIND = "research.audience@1"
MARKET_KIND = "research.market@1"
POSITIONING_KIND = "strategy.positioning@1"
PLAN_KIND = "strategy.plan@1"
CORE_COPY_KIND = "content.core_copy@1"
ADS_KIND = "content.ads@1"
SOCIAL_KIND = "content.social@1"
EMAIL_KIND = "content.email_sequence@1"
VISUALS_KIND = "creative.visual_concepts@1"
REPORT_KIND = "report.campaign_brief@1"
COMPETITORS_KIND = "research.competitors@1"
FACT_CHECK_KIND = "quality.fact_check@1"
BRAND_CHECK_KIND = "quality.brand_check@1"

_HASHTAG_RE = re.compile(r"[^\w]+", re.UNICODE)
_clip = clip
_cell = cell


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #
_context = context_of


class Finding(BaseModel):
    """One research statement, its basis, and the evidence it cites.

    An ``external_source`` finding must cite evidence ids. When the agent passes
    the ids it actually retrieved as validation context, every cited id must be
    one of them. An invented citation is therefore a validation error, which the
    repair loop sends back to the model. With no evidence in context (assumptions
    mode), external findings are impossible.
    """

    statement: Text
    basis: Literal["user_provided", "model_assumption", "external_source"] = "model_assumption"
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _citations(self, info: ValidationInfo) -> Finding:
        if self.basis == "external_source" and not self.evidence_ids:
            raise ValueError("an external_source finding must cite evidence_ids")
        known = _context(info).get("evidence_ids")
        if known is not None:
            unknown = [e for e in self.evidence_ids if e not in known]
            if unknown:
                raise ValueError(
                    f"unknown evidence ids {unknown}; cite only the [ev-...] ids listed in the "
                    "evidence (or use basis model_assumption with no ids)"
                )
        return self

    def to_claim(self) -> Claim:
        return Claim(
            statement=self.statement, basis=Basis(self.basis), evidence_ids=tuple(self.evidence_ids)
        )


class ResearchPlan(BaseModel):
    """The search queries a research agent proposes (executed by code, not the model)."""

    queries: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    ] = Field(min_length=1, max_length=3)


class Persona(BaseModel):
    name: Short
    description: Text
    motivations: list[Text] = Field(min_length=1, max_length=5)
    pain_points: list[Text] = Field(min_length=1, max_length=5)
    objections: list[Text] = Field(default_factory=list, max_length=5)
    preferred_channels: list[Short] = Field(default_factory=list, max_length=6)


class AudienceResearch(BaseModel):
    summary: Long
    personas: list[Persona] = Field(min_length=1, max_length=4)
    insights: list[Finding] = Field(min_length=1, max_length=8)

    def claims(self) -> tuple[Claim, ...]:
        return tuple(f.to_claim() for f in self.insights)

    def digest(self) -> str:
        lines = [f"Audience summary: {_clip(self.summary, 300)}"]
        for p in self.personas:
            lines.append(
                f"- Persona {p.name}: {_clip(p.description, 120)} Pain points: "
                + "; ".join(_clip(x, 60) for x in p.pain_points[:3])
            )
        lines.extend(f"- Insight ({f.basis}): {_clip(f.statement, 140)}" for f in self.insights[:5])
        return "\n".join(lines)


class MarketResearch(BaseModel):
    category_summary: Long
    trends: list[Finding] = Field(min_length=1, max_length=6)
    competitor_angles: list[Finding] = Field(default_factory=list, max_length=5)
    positioning_angles: list[Text] = Field(min_length=2, max_length=5)

    def claims(self) -> tuple[Claim, ...]:
        return tuple(f.to_claim() for f in (*self.trends, *self.competitor_angles))

    def digest(self) -> str:
        lines = [f"Market: {_clip(self.category_summary, 300)}"]
        lines.extend(f"- Trend ({t.basis}): {_clip(t.statement, 140)}" for t in self.trends[:4])
        lines.extend(f"- Angle: {_clip(a, 140)}" for a in self.positioning_angles)
        return "\n".join(lines)


class CompetitorProfile(BaseModel):
    name: Short
    summary: Text
    positioning: Finding
    strengths: list[Finding] = Field(default_factory=list, max_length=4)
    weaknesses: list[Finding] = Field(default_factory=list, max_length=4)


class CompetitorResearch(BaseModel):
    """Competitor profiles; only competitors named in the reference material are allowed."""

    competitors: list[CompetitorProfile] = Field(default_factory=list, max_length=5)
    opportunities: list[Finding] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def _named_competitors_only(self, info: ValidationInfo) -> CompetitorResearch:
        reference = _context(info).get("reference_text")
        if reference is None:
            return self
        invented = [c.name for c in self.competitors if c.name.casefold() not in reference]
        if invented:
            raise ValueError(
                f"competitors {invented} are not named in the brief, the user's sources or the "
                "evidence; remove them (an empty competitors list is fine)"
            )
        return self

    def claims(self) -> tuple[Claim, ...]:
        findings = [
            f for c in self.competitors for f in (c.positioning, *c.strengths, *c.weaknesses)
        ]
        return tuple(f.to_claim() for f in (*findings, *self.opportunities))

    def digest(self) -> str:
        if not self.competitors:
            lines = ["No named competitors were found in the brief, sources or evidence."]
        else:
            lines = [f"- {c.name}: {_clip(c.positioning.statement, 140)}" for c in self.competitors]
        lines.extend(f"- Opportunity: {_clip(o.statement, 140)}" for o in self.opportunities)
        return "\n".join(lines)

    def to_markdown(self) -> str:
        if not self.competitors:
            table = "_No named competitors were found in the brief, sources or evidence._"
        else:
            rows = "\n".join(
                f"| {_cell(c.name)} | {_cell(c.positioning.statement)} | "
                f"{_cell('; '.join(s.statement for s in c.strengths)) or '-'} | "
                f"{_cell('; '.join(w.statement for w in c.weaknesses)) or '-'} |"
                for c in self.competitors
            )
            table = (
                "| Competitor | Positioning | Strengths | Weaknesses |\n"
                "| --- | --- | --- | --- |\n" + rows
            )
        opportunities = "\n".join(f"- {o.statement}" for o in self.opportunities)
        return f"{table}\n\n**Opportunities:**\n{opportunities}"


class ClaimVerdict(BaseModel):
    """The fact checker model's verdict on one numbered claim."""

    index: int = Field(ge=0)
    verdict: Literal["supported", "partially_supported", "unsupported"]
    note: str = Field(default="", max_length=400)


class FactCheckVerdicts(BaseModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _indices(self, info: ValidationInfo) -> FactCheckVerdicts:
        count = _context(info).get("claim_count")
        if count is None:
            return self
        indices = [v.index for v in self.verdicts]
        bad = sorted({i for i in indices if i >= count})
        duplicate = sorted({i for i in indices if indices.count(i) > 1})
        if bad or duplicate:
            raise ValueError(
                f"verdict indices must be 0-{count - 1}, each once; "
                f"out of range: {bad}, repeated: {duplicate}"
            )
        return self


class CheckedClaim(BaseModel):
    statement: Text
    evidence_ids: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "partially_supported", "unsupported", "not_checked"]
    note: str = ""


class FactCheck(BaseModel):
    """Claim-by-claim verification against cited evidence. The counts are computed by code."""

    claims: list[CheckedClaim] = Field(default_factory=list, max_length=40)
    supported: int = 0
    partially_supported: int = 0
    unsupported: int = 0
    not_checked: int = 0

    @model_validator(mode="after")
    def _count(self) -> FactCheck:
        for name in ("supported", "partially_supported", "unsupported", "not_checked"):
            setattr(self, name, sum(1 for c in self.claims if c.verdict == name))
        return self

    def digest(self) -> str:
        return (
            f"{len(self.claims)} claim(s): {self.supported} supported, "
            f"{self.partially_supported} partly, {self.unsupported} unsupported, "
            f"{self.not_checked} not checked"
        )

    def to_markdown(self) -> str:
        if not self.claims:
            return "_No externally sourced claims to check._"
        rows = "\n".join(
            f"| {_cell(c.statement)} | {c.verdict.replace('_', ' ')} | "
            f"{_cell(', '.join(c.evidence_ids)) or '-'} | {_cell(c.note) or '-'} |"
            for c in self.claims
        )
        return "| Claim | Verdict | Evidence | Note |\n| --- | --- | --- | --- |\n" + rows


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #
class MessagingPillar(BaseModel):
    name: Short
    message: Text
    proof_points_needed: list[Text] = Field(default_factory=list, max_length=4)


class BenefitRung(BaseModel):
    feature: Text
    benefit: Text
    emotional_payoff: Text


class Positioning(BaseModel):
    positioning_statement: Long
    value_propositions: list[Text] = Field(min_length=2, max_length=4)
    messaging_pillars: list[MessagingPillar] = Field(min_length=2, max_length=4)
    benefit_ladder: list[BenefitRung] = Field(min_length=1, max_length=5)
    differentiators: list[Text] = Field(min_length=1, max_length=5)

    def digest(self) -> str:
        lines = [f"Positioning: {_clip(self.positioning_statement, 300)}"]
        lines.extend(f"- Value proposition: {_clip(v, 140)}" for v in self.value_propositions)
        lines.extend(f"- Pillar {p.name}: {_clip(p.message, 140)}" for p in self.messaging_pillars)
        return "\n".join(lines)


class ChannelPlan(BaseModel):
    channel: Short
    role: Text
    weight: int = Field(ge=1, le=10)
    kpis: list[Text] = Field(min_length=1, max_length=4)
    share_percent: int | None = Field(default=None, ge=0, le=100)


class Phase(BaseModel):
    name: Short
    weeks: int = Field(ge=1, le=12)
    focus: Text
    activities: list[Text] = Field(min_length=1, max_length=6)


def allocate_shares(weights: list[int]) -> list[int]:
    """Integer percentages proportional to ``weights`` that sum to exactly 100.

    Uses the largest-remainder method. ADR-011: the model proposes relative
    weights; code does the arithmetic.
    """
    total = sum(weights)
    raw = [w * 100 / total for w in weights]
    shares = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - shares[i], -i), reverse=True)
    for i in order[: 100 - sum(shares)]:
        shares[i] += 1
    return shares


class MarketingPlan(BaseModel):
    objectives: list[Text] = Field(min_length=1, max_length=4)
    kpis: list[Text] = Field(min_length=1, max_length=6)
    # As many channels as a brief may list (the model cannot be asked to drop any).
    channels: list[ChannelPlan] = Field(min_length=1, max_length=MAX_CHANNELS)
    phases: list[Phase] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _derive(self) -> MarketingPlan:
        names = [c.channel.casefold() for c in self.channels]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"each channel must appear once; duplicated: {', '.join(duplicates)}")
        for channel, share in zip(
            self.channels, allocate_shares([c.weight for c in self.channels]), strict=True
        ):
            channel.share_percent = share
        return self

    @property
    def total_weeks(self) -> int:
        return sum(p.weeks for p in self.phases)

    def digest(self) -> str:
        lines = ["Objectives: " + "; ".join(_clip(o, 100) for o in self.objectives)]
        lines.extend(
            f"- {c.channel}: {c.share_percent}% of effort ({_clip(c.role, 100)})"
            for c in self.channels
        )
        lines.append(f"Timeline: {self.total_weeks} weeks in {len(self.phases)} phase(s)")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {_cell(c.channel)} | {c.share_percent}% | {_cell(c.role)} | "
            f"{_cell('; '.join(c.kpis))} |"
            for c in self.channels
        )
        phases = "\n".join(
            f"{i}. **{_cell(p.name)}** ({p.weeks} wk): {_cell(p.focus)}"
            for i, p in enumerate(self.phases, 1)
        )
        return (
            "| Channel | Share of effort | Role | KPIs |\n| --- | --- | --- | --- |\n"
            f"{rows}\n\n{phases}"
        )


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
class CoreCopy(BaseModel):
    tagline: Short
    primary_message: Long
    headlines: list[Short] = Field(min_length=3, max_length=5)
    body_copy: Long
    call_to_action: Short

    @model_validator(mode="after")
    def _no_banned_terms(self, info: ValidationInfo) -> CoreCopy:
        """With a brand in play (validation context ``banned_terms``), banned terms are errors."""
        banned = _context(info).get("banned_terms") or ()
        used = sorted(term for term, n in term_counts(self.text(), banned).items() if n)
        if used:
            raise ValueError(
                f"the copy uses the brand's banned term(s) {', '.join(map(repr, used))}; "
                "rewrite it without them"
            )
        return self

    def text(self) -> str:
        """Every line of copy, for vocabulary checks."""
        parts = [self.tagline, self.primary_message, *self.headlines, self.body_copy]
        return "\n".join([*parts, self.call_to_action])

    def digest(self) -> str:
        return (
            f"Tagline: {self.tagline}\nPrimary message: {_clip(self.primary_message, 300)}\n"
            f"Headlines: {' | '.join(self.headlines)}\nCall to action: {self.call_to_action}"
        )

    def to_markdown(self) -> str:
        headlines = "\n".join(f"- {h}" for h in self.headlines)
        return (
            f"**Tagline:** {self.tagline}\n\n**Primary message:** {self.primary_message}\n\n"
            f"**Headlines:**\n{headlines}\n\n**Body copy:** {self.body_copy}\n\n"
            f"**Call to action:** {self.call_to_action}"
        )


#: Character limits by platform. "hard" limits are enforced (the repair loop asks
#: the model to shorten); "recommended" limits only produce warnings. These are the
#: commonly documented limits; verify a platform's current rules before publishing.
PLATFORM_LIMITS: dict[str, dict[str, tuple[int, Literal["hard", "recommended"]]]] = {
    "google_search": {"headline": (30, "hard"), "description": (90, "hard")},
    "meta": {"headline": (40, "recommended"), "primary_text": (125, "recommended")},
    "linkedin": {"headline": (70, "recommended"), "primary_text": (150, "recommended")},
    "x": {"primary_text": (280, "hard")},
    "tiktok": {"primary_text": (100, "recommended")},
}


def platform_key(name: str) -> str | None:
    """Map a free-form platform or channel name to a key in :data:`PLATFORM_LIMITS`."""
    value = name.casefold()
    if "google" in value or value in {"search", "sem", "ppc"}:
        return "google_search"
    if any(k in value for k in ("facebook", "instagram", "meta")):
        return "meta"
    if "linkedin" in value:
        return "linkedin"
    if value in {"x", "twitter", "x (twitter)"} or "twitter" in value:
        return "x"
    if "tiktok" in value:
        return "tiktok"
    return None


#: At most this many channels get ads (2 variants each fits the 12-variant pack).
MAX_AD_CHANNELS = 6


def ad_channels(channels: list[str]) -> list[str]:
    """The channels that get ads: ad-capable platforms first, at most :data:`MAX_AD_CHANNELS`.

    Chosen by code so the prompt and the output contract can never disagree.
    """
    capable = [c for c in channels if platform_key(c) is not None]
    others = [c for c in channels if platform_key(c) is None]
    return (capable + others)[:MAX_AD_CHANNELS]


class AdVariant(BaseModel):
    platform: Short
    headline: Text
    primary_text: Long
    description: str = Field(default="", max_length=800)
    call_to_action: Short
    warnings: list[str] = Field(default_factory=list)


class AdPack(BaseModel):
    variants: list[AdVariant] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def _check_limits(self) -> AdPack:
        violations: list[str] = []
        for number, variant in enumerate(self.variants, 1):
            variant.warnings = []
            key = platform_key(variant.platform)
            for field, (limit, kind) in PLATFORM_LIMITS.get(key or "", {}).items():
                length = len(str(getattr(variant, field)))
                if length <= limit:
                    continue
                message = (
                    f"variant {number} ({variant.platform}) {field} is {length} characters; "
                    f"the limit is {limit}"
                )
                if kind == "hard":
                    violations.append(message)
                else:
                    variant.warnings.append(message + " (recommended)")
        if violations:
            raise ValueError("shorten these fields: " + "; ".join(violations))
        return self

    def digest(self) -> str:
        return "\n".join(f"- {v.platform}: {_clip(v.headline, 80)}" for v in self.variants)

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {_cell(v.platform)} | {_cell(v.headline)} | {_cell(v.primary_text)} | "
            f"{_cell(v.call_to_action)} | {_cell('; '.join(v.warnings)) or '-'} |"
            for v in self.variants
        )
        return (
            "| Platform | Headline | Primary text | CTA | Notes |\n"
            "| --- | --- | --- | --- | --- |\n" + rows
        )


class SocialPost(BaseModel):
    platform: Short
    day: int = Field(ge=1, le=28)
    format: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    caption: Long
    hashtags: list[str] = Field(default_factory=list, max_length=12)
    visual_idea: Text
    week: int | None = None

    @field_validator("hashtags", mode="before")
    @classmethod
    def _normalise_hashtags(cls, value: Any) -> list[str]:
        items = value.split() if isinstance(value, str) else list(value or [])
        tags: list[str] = []
        for item in items:
            tag = _HASHTAG_RE.sub("", str(item))
            if tag and f"#{tag}".casefold() not in {t.casefold() for t in tags}:
                tags.append(f"#{tag}")
        return tags


class SocialCalendar(BaseModel):
    weeks: int = Field(ge=1, le=4)
    posts: list[SocialPost] = Field(min_length=3, max_length=20)

    @model_validator(mode="after")
    def _derive(self) -> SocialCalendar:
        problems = [
            f"post on day {p.day} is outside the {self.weeks}-week calendar"
            for p in self.posts
            if p.day > self.weeks * 7
        ]
        problems.extend(
            f"post on day {p.day} for {p.platform} is {len(p.caption)} characters; the limit is 280"
            for p in self.posts
            if platform_key(p.platform) == "x" and len(p.caption) > 280
        )
        if problems:
            raise ValueError("; ".join(problems))
        for post in self.posts:
            post.week = (post.day - 1) // 7 + 1
        self.posts.sort(key=lambda p: (p.day, p.platform.casefold()))
        return self

    def digest(self) -> str:
        platforms = sorted({p.platform for p in self.posts})
        return f"{len(self.posts)} posts over {self.weeks} week(s) on {', '.join(platforms)}"

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {p.week} | {p.day} | {_cell(p.platform)} | {_cell(p.format)} | "
            f"{_cell(p.caption)} | "
            f"{_cell(' '.join(p.hashtags)) or '-'} |"
            for p in self.posts
        )
        return (
            "| Week | Day | Platform | Format | Caption | Hashtags |\n"
            "| --- | --- | --- | --- | --- | --- |\n" + rows
        )


class Email(BaseModel):
    send_day: int = Field(ge=0, le=60)
    purpose: Text
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    preview_text: str = Field(default="", max_length=200)
    body: Long
    call_to_action: Short
    warnings: list[str] = Field(default_factory=list)


class EmailSequence(BaseModel):
    name: Short
    emails: list[Email] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def _derive(self) -> EmailSequence:
        self.emails.sort(key=lambda e: e.send_day)
        for email in self.emails:
            email.warnings = []
            if len(email.subject) > 60:
                email.warnings.append(
                    f"subject is {len(email.subject)} characters (60 recommended)"
                )
            if len(email.preview_text) > 90:
                email.warnings.append(
                    f"preview text is {len(email.preview_text)} characters (90 recommended)"
                )
        return self

    def digest(self) -> str:
        return f"{self.name}: " + "; ".join(
            f"day {e.send_day} '{_clip(e.subject, 60)}'" for e in self.emails
        )

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| {e.send_day} | {_cell(e.purpose)} | {_cell(e.subject)} | "
            f"{_cell(e.preview_text) or '-'} | "
            f"{_cell(e.call_to_action)} |"
            for e in self.emails
        )
        return (
            f"**{_cell(self.name)}**\n\n| Day | Purpose | Subject | Preview | CTA |\n"
            "| --- | --- | --- | --- | --- |\n" + rows
        )


# --------------------------------------------------------------------------- #
# Creative
# --------------------------------------------------------------------------- #
class VisualConcept(BaseModel):
    name: Short
    description: Text
    image_prompt: str = Field(max_length=1000)
    mood: Short
    palette: list[Short] = Field(min_length=1, max_length=6)
    aspect_ratio: str = Field(default="1:1", pattern=r"^\d{1,2}:\d{1,2}$")

    @field_validator("image_prompt")
    @classmethod
    def _single_line_and_specific(cls, value: str) -> str:
        line = " ".join(value.split())
        if len(line.split()) < 8:
            raise ValueError(
                "image_prompt must be a specific single-line prompt of at least 8 words"
            )
        return line


class VisualConcepts(BaseModel):
    concepts: list[VisualConcept] = Field(min_length=3, max_length=5)

    def digest(self) -> str:
        return "\n".join(f"- {c.name}: {_clip(c.description, 120)}" for c in self.concepts)

    def to_markdown(self) -> str:
        return "\n".join(
            f"{i}. **{_cell(c.name)}** ({c.aspect_ratio}, {_cell(c.mood)}): `{c.image_prompt}`"
            for i, c in enumerate(self.concepts, 1)
        )


# --------------------------------------------------------------------------- #
# Brand
# --------------------------------------------------------------------------- #
#: Minimum model-judged tone score (0-1) for a brand check to pass.
BRAND_TONE_THRESHOLD = 0.5


class TermHit(BaseModel):
    term: str
    deliverable: str
    count: int = Field(ge=1)


class ToneVerdict(BaseModel):
    """What the model judge returns for a brand check: one 1-5 score and short notes."""

    score: int = Field(ge=1, le=5)
    notes: list[Text] = Field(default_factory=list, max_length=5)


class BrandCheck(BaseModel):
    """Vocabulary checked by code plus a model-judged tone score; ``passed`` is set by code."""

    brand: Short
    checked: list[str] = Field(default_factory=list)
    banned_hits: list[TermHit] = Field(default_factory=list)
    preferred_used: list[str] = Field(default_factory=list)
    preferred_missing: list[str] = Field(default_factory=list)
    tone_score: float | None = Field(default=None, ge=0.0, le=1.0)
    tone_notes: list[str] = Field(default_factory=list, max_length=5)
    passed: bool = False

    @model_validator(mode="after")
    def _verdict(self) -> BrandCheck:
        tone_ok = self.tone_score is None or self.tone_score >= BRAND_TONE_THRESHOLD
        self.passed = not self.banned_hits and tone_ok
        return self

    def digest(self) -> str:
        preferred = len(self.preferred_used) + len(self.preferred_missing)
        tone = "not judged" if self.tone_score is None else f"{self.tone_score:.2f}"
        return (
            f"Brand check ({self.brand}): {'passed' if self.passed else 'needs work'}; "
            f"{sum(h.count for h in self.banned_hits)} banned-term use(s); tone {tone}; "
            f"preferred terms used {len(self.preferred_used)}/{preferred}"
        )

    def to_markdown(self) -> str:
        lines = [f"**Result:** {self.digest()}"]
        if self.banned_hits:
            rows = "\n".join(
                f"| {_cell(hit.term)} | {_cell(hit.deliverable)} | {hit.count} |"
                for hit in self.banned_hits
            )
            lines.append(
                "**Banned terms found:**\n\n| Term | Deliverable | Uses |\n| --- | --- | --- |\n"
                + rows
            )
        if self.preferred_missing:
            lines.append(f"**Preferred terms not used:** {', '.join(self.preferred_missing)}")
        if self.tone_notes:
            lines.append("**Tone notes:**\n" + "\n".join(f"- {n}" for n in self.tone_notes))
        return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
class CampaignReport(BaseModel):
    title: Short
    markdown: str = Field(min_length=1)
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    def digest(self) -> str:
        return f"{self.title} ({len(self.markdown.split())} words)"


MODELS_BY_KIND: dict[str, type[BaseModel]] = {
    BRIEF_KIND: CampaignBrief,
    AUDIENCE_KIND: AudienceResearch,
    MARKET_KIND: MarketResearch,
    POSITIONING_KIND: Positioning,
    PLAN_KIND: MarketingPlan,
    CORE_COPY_KIND: CoreCopy,
    ADS_KIND: AdPack,
    SOCIAL_KIND: SocialCalendar,
    EMAIL_KIND: EmailSequence,
    VISUALS_KIND: VisualConcepts,
    REPORT_KIND: CampaignReport,
    REVIEW_KIND: ReviewResult,
    SOURCES_KIND: SourceBundle,
    COMPETITORS_KIND: CompetitorResearch,
    FACT_CHECK_KIND: FactCheck,
    BRAND_KIND: BrandProfile,
    BRAND_CHECK_KIND: BrandCheck,
    **WAVE2_MODELS,
}
