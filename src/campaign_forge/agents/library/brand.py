"""Brand agents (Session 5): the brand strategist and the brand consistency check.

* ``brand_strategist`` writes a :class:`~campaign_forge.domain.brand.BrandProfile`
  (``brand.profile@1``) from the brief, plus audience research when present.
* ``brand_consistency`` checks deliverables against a profile
  (``quality.brand_check@1``). Banned and preferred terms are counted by code in
  every content field. Only the tone score comes from a model judge, and code
  decides whether the check passed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.brand import BRAND_KIND, BrandProfile, term_counts
from ...providers.base import CompletionRequest, ModelClass
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import (
    ADS_KIND,
    AUDIENCE_KIND,
    BRAND_CHECK_KIND,
    CORE_COPY_KIND,
    EMAIL_KIND,
    MODELS_BY_KIND,
    SOCIAL_KIND,
    BrandCheck,
    TermHit,
    ToneVerdict,
)
from .common import (
    BRIEF,
    AgentCategory,
    StructuredAgent,
    brief_of,
    context,
    framed_brief,
    make_spec,
    produced_by,
)

BRAND = InputSpec(name="brand", kind=BRAND_KIND)
_OPTIONAL_AUDIENCE = InputSpec(name="audience", kind=AUDIENCE_KIND, required=False)

#: Deliverables a brand check reads: (input name, kind, required).
CHECKED: tuple[tuple[str, str, bool], ...] = (
    ("copy", CORE_COPY_KIND, True),
    ("ads", ADS_KIND, False),
    ("social", SOCIAL_KIND, False),
    ("email", EMAIL_KIND, False),
)
#: Data fields that hold values written by code (labels, numbers), not content.
_SKIP_FIELDS = frozenset({"platform", "format", "warnings", "weeks", "day", "send_day"})
_MAX_REVIEW_CHARS = 5000


def content_text(data: Any) -> str:
    """Every content string in an artifact's data, skipping fields written by code."""
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                if key not in _SKIP_FIELDS:
                    walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(data)
    return "\n".join(parts)


def _rendered(artifact: Artifact) -> str:
    model = MODELS_BY_KIND.get(artifact.kind)
    if model is not None:
        to_markdown = getattr(artifact.as_model(model), "to_markdown", None)
        if callable(to_markdown):
            return str(to_markdown())
    return content_text(artifact.data)


class BrandStrategistAgent(StructuredAgent):
    spec = make_spec(
        "brand_strategist",
        "Brand strategist",
        "A brand profile: voice, tone words, values, always/never guidelines, preferred and "
        "banned terms, and visual style.",
        category=AgentCategory.STRATEGY,
        inputs=(BRIEF, _OPTIONAL_AUDIENCE),
        output_kind=BRAND_KIND,
        capabilities=("branding", "brand_voice", "style_guide"),
        rubric=("on_brief", "distinctiveness", "usability", "consistency"),
        max_tokens=900,
        reviewable=True,
    )
    output_model: ClassVar[type[BrandProfile]] = BrandProfile
    role = (
        "You are a brand strategist. You turn a product brief into a practical voice guide "
        "that any writer can follow, with concrete words to use and words to avoid."
    )
    shape = (
        '{"name": "...", "voice": "...", "tone_words": ["..."], "audience": "...", '
        '"values": ["..."], "guidelines": ["..."], "avoid": ["..."], '
        '"preferred_terms": ["..."], "banned_terms": ["..."], "visual_style": "..."}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        brief = brief_of(inputs)
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'audience')}\n\n"
            f"Task: write a brand profile for {brief.product_name}, named after the brand or "
            f"product. Describe the voice in 2-3 sentences that fit this tone: {brief.tone}. "
            "Give 3-5 tone words, 3-5 values, 3-6 'guidelines' (what the content always does), "
            "3-6 'avoid' items (what it never does), 3-8 preferred terms, and 3-8 banned terms: "
            "words or short phrases the brand never uses, such as cliches or claims the brief "
            "cannot support. No term may be both preferred and banned. End with one sentence "
            "on visual style."
        )

    def notes(self, value: Any) -> tuple[str, ...]:
        return (
            f"{len(value.preferred_terms)} preferred and {len(value.banned_terms)} banned term(s)",
        )


class BrandConsistencyAgent:
    """Checks deliverables against a brand profile.

    It passes when no banned term appears in any deliverable and the model-judged
    tone score reaches ``BRAND_TONE_THRESHOLD``. One model call, for the tone only.
    """

    spec = make_spec(
        "brand_consistency",
        "Brand consistency",
        "Checks content against a brand profile: banned and preferred terms counted by code, "
        "tone scored by a model judge.",
        category=AgentCategory.QUALITY,
        inputs=(
            BRAND,
            *(InputSpec(name=name, kind=kind, required=req) for name, kind, req in CHECKED),
        ),
        output_kind=BRAND_CHECK_KIND,
        capabilities=("brand_consistency", "brand_safety", "quality_assurance"),
        rubric=("calibration",),
        max_tokens=400,
        model_class=ModelClass.SMALL,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        brand = inputs["brand"].as_model(BrandProfile)
        texts = {name: content_text(inputs[name].data) for name, _, _ in CHECKED if name in inputs}
        hits = [
            TermHit(term=term, deliverable=name, count=count)
            for name, text in texts.items()
            for term, count in term_counts(text, brand.banned_terms).items()
            if count
        ]
        used = term_counts("\n".join(texts.values()), brand.preferred_terms)
        work = "\n\n".join(f"{name}:\n{_rendered(inputs[name])}" for name in texts)
        system = with_untrusted_policy(
            "You are a brand guardian. You judge only whether the work sounds the way the brand "
            "profile describes. Respond with one JSON object."
        )
        user = (
            f"Brand profile:\n{frame_untrusted(brand.digest(), label='brand profile')}\n\n"
            "Work to check:\n"
            f"{frame_untrusted(work[:_MAX_REVIEW_CHARS], label='work under review')}\n\n"
            "Score from 1 (off-brand) to 5 (unmistakably this brand) how well the work matches "
            "the brand's voice and tone words. Give up to 5 short, specific notes.\n\n"
            'Return ONLY a JSON object: {"score": 4, "notes": ["..."]}'
        )
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), ToneVerdict
        )
        check = BrandCheck(
            brand=brand.name,
            checked=list(texts),
            banned_hits=hits,
            preferred_used=[t for t in brand.preferred_terms if used.get(t)],
            preferred_missing=[t for t in brand.preferred_terms if not used.get(t)],
            tone_score=round((result.value.score - 1) / 4, 3),
            tone_notes=list(result.value.notes),
        )
        artifact = Artifact.from_model(
            BRAND_CHECK_KIND,
            check,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        return AgentOutput(artifact=artifact, notes=(check.digest(),))
