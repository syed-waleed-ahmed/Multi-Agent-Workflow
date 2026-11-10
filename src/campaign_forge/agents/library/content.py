"""Content agents (wave 1): core copy, ads, social calendar and email sequence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ..schemas import (
    ADS_KIND,
    AUDIENCE_KIND,
    CORE_COPY_KIND,
    EMAIL_KIND,
    PLATFORM_LIMITS,
    POSITIONING_KIND,
    SOCIAL_KIND,
    AdPack,
    CoreCopy,
    EmailSequence,
    SocialCalendar,
    ad_channels,
    platform_key,
)
from .common import (
    BRIEF,
    OPTIONAL_BRAND,
    AgentCategory,
    StructuredAgent,
    brand_of,
    brief_of,
    context,
    framed_brand,
    framed_brief,
    make_spec,
)

POSITIONING = InputSpec(name="positioning", kind=POSITIONING_KIND)
OPTIONAL_POSITIONING = InputSpec(name="positioning", kind=POSITIONING_KIND, required=False)
OPTIONAL_AUDIENCE = InputSpec(name="audience", kind=AUDIENCE_KIND, required=False)
COPY = InputSpec(name="copy", kind=CORE_COPY_KIND)


def _limits_text(channels: list[str]) -> str:
    lines = []
    for channel in channels:
        key = platform_key(channel)
        if key is None:
            continue
        rules = ", ".join(
            f"{field} max {limit} characters{'' if kind == 'hard' else ' (recommended)'}"
            for field, (limit, kind) in PLATFORM_LIMITS[key].items()
        )
        lines.append(f"- {channel}: {rules}")
    return "Platform limits:\n" + "\n".join(lines) if lines else ""


class CopywriterAgent(StructuredAgent):
    spec = make_spec(
        "copywriter",
        "Copywriter",
        "Campaign core copy: tagline, primary message, headlines, body copy and CTA.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, POSITIONING, OPTIONAL_AUDIENCE, OPTIONAL_BRAND),
        output_kind=CORE_COPY_KIND,
        capabilities=("copywriting", "tagline", "headlines"),
        rubric=("on_brief", "clarity", "persuasiveness", "tone_match", "memorability"),
        max_tokens=800,
        reviewable=True,
    )
    output_model: ClassVar[type[CoreCopy]] = CoreCopy
    role = (
        "You are an award-winning copywriter. You write concise, specific, persuasive copy "
        "that sounds human and matches the requested tone."
    )
    shape = (
        '{"tagline": "...", "primary_message": "...", "headlines": ["...", "...", "..."], '
        '"body_copy": "...", "call_to_action": "..."}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        brief = brief_of(inputs)
        brand_rule = (
            " Write in the brand profile's voice, use its preferred terms where they fit, and "
            "never use its banned terms."
            if "brand" in inputs
            else ""
        )
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n{framed_brand(inputs)}"
            f"{context(inputs, 'positioning', 'audience')}\n\n"
            f"Task: write the campaign's core copy in a {brief.tone} tone: a tagline, a 2-3 "
            "sentence primary message, 3-5 headlines, a body-copy paragraph, and a call to "
            f"action. Mention {brief.product_name} by name. Only claim what the brief supports."
            f"{brand_rule}"
        )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        brand = brand_of(inputs)
        if brand is None or not brand.banned_terms:
            return self.produce(inputs, ctx)
        # A banned term is a contract violation, so the repair loop asks for a rewrite.
        return self.produce(
            inputs, ctx, validation_context={"banned_terms": list(brand.banned_terms)}
        )


class AdCopyAgent(StructuredAgent):
    spec = make_spec(
        "ad_copy",
        "Ad copy",
        "Platform-specific ad variants; character limits are checked by code.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, COPY, OPTIONAL_POSITIONING),
        output_kind=ADS_KIND,
        capabilities=("advertising", "paid_social", "search_ads"),
        rubric=("on_brief", "platform_fit", "persuasiveness", "tone_match"),
        max_tokens=1200,
        reviewable=True,
    )
    output_model: ClassVar[type[AdPack]] = AdPack
    role = (
        "You are a performance-marketing copywriter. You write ads that fit each platform's "
        "format and limits exactly."
    )
    shape = (
        '{"variants": [{"platform": "...", "headline": "...", "primary_text": "...", '
        '"description": "...", "call_to_action": "..."}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        channels = ad_channels(brief_of(inputs).channels)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'copy', 'positioning')}\n\n"
            f"Task: write 2 ad variants for each of these channels: {', '.join(channels)}. "
            "Keep the core message consistent but adapt the format to each platform.\n"
            f"{_limits_text(channels)}"
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        warnings = sum(len(v.warnings) for v in value.variants)
        return (f"{len(value.variants)} variants; {warnings} recommended-limit warning(s)",)


class SocialMediaAgent(StructuredAgent):
    spec = make_spec(
        "social_media",
        "Social media",
        "Platform-specific posts arranged as a content calendar (weeks computed by code).",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, COPY, OPTIONAL_POSITIONING),
        output_kind=SOCIAL_KIND,
        capabilities=("social_media", "content_calendar"),
        rubric=("on_brief", "platform_fit", "variety", "tone_match"),
        max_tokens=1500,
        reviewable=True,
    )
    output_model: ClassVar[type[SocialCalendar]] = SocialCalendar
    role = (
        "You are a social media manager. You plan varied, platform-native posts that build "
        "toward the campaign goal."
    )
    shape = (
        '{"weeks": 2, "posts": [{"platform": "...", "day": 1, "format": "post", '
        '"caption": "...", "hashtags": ["..."], "visual_idea": "..."}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        brief = brief_of(inputs)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'copy', 'positioning')}\n\n"
            "Task: plan a 2-week social calendar with 5-8 posts across these channels where "
            f"they make sense: {', '.join(brief.channels)}. Use day numbers 1-14, vary the "
            "formats (post, reel, story, carousel, thread), keep captions under 220 characters and "
            "X posts under 280, and use at most 4 hashtags per post."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)


class EmailMarketingAgent(StructuredAgent):
    spec = make_spec(
        "email_marketing",
        "Email marketing",
        "An email sequence with send-day offsets, subjects, preview text, body and CTA.",
        category=AgentCategory.CONTENT,
        inputs=(BRIEF, COPY, OPTIONAL_AUDIENCE),
        output_kind=EMAIL_KIND,
        capabilities=("email", "lifecycle", "nurture"),
        rubric=("on_brief", "sequence_logic", "persuasiveness", "tone_match"),
        max_tokens=1500,
        reviewable=True,
    )
    output_model: ClassVar[type[EmailSequence]] = EmailSequence
    role = (
        "You are a lifecycle email marketer. Each email in your sequences has one job and "
        "earns the next open."
    )
    shape = (
        '{"name": "...", "emails": [{"send_day": 0, "purpose": "...", "subject": "...", '
        '"preview_text": "...", "body": "...", "call_to_action": "..."}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'copy', 'audience')}\n\n"
            "Task: write a 3-4 email sequence for this campaign. Give each email a send day "
            "(0 = launch), a single purpose, a subject under 60 characters, preview text "
            "under 90 characters, a body under 90 words, and a call to action."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (value.digest(),)
