"""Quality agents, wave 2: the safety reviewer and the CRO advisor.

* ``safety_reviewer`` combines rule flags, matched by code, with model flags for
  what rules miss (for example discriminatory targeting). A model flag is kept
  only if its quote really appears in the deliverable it names, so a model cannot
  flag text that is not there. It raises flags only and gives no legal advice.
* ``cro_advisor`` scores a landing page against a checklist computed by code. The
  model only proposes and prioritises changes.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...exceptions import AgentError
from ...providers.base import CompletionRequest, ModelClass
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import ADS_KIND, BRIEF_KIND, CORE_COPY_KIND, EMAIL_KIND, SOCIAL_KIND
from ..schemas_wave2 import (
    ARTICLE_KIND,
    CRO_KIND,
    LANDING_KIND,
    PRODUCT_COPY_KIND,
    REPURPOSED_KIND,
    SAFETY_KIND,
    CroAdvice,
    CroCheck,
    CroRecommendations,
    LandingPage,
    ModelSafetyFlags,
    SafetyFlag,
    SafetyReview,
    quoted_in,
    rule_flags,
)
from .brand import content_text
from .common import AgentCategory, make_spec, produced_by

#: Deliverables the safety reviewer reads: (input name, kind).
SAFETY_INPUTS: tuple[tuple[str, str], ...] = (
    ("copy", CORE_COPY_KIND),
    ("ads", ADS_KIND),
    ("social", SOCIAL_KIND),
    ("email", EMAIL_KIND),
    ("landing", LANDING_KIND),
    ("product", PRODUCT_COPY_KIND),
    ("repurposed", REPURPOSED_KIND),
    ("article", ARTICLE_KIND),
)
_MAX_REVIEW_CHARS = 6000


class SafetyReviewerAgent:
    spec = make_spec(
        "safety_reviewer",
        "Safety reviewer",
        "Flags regulated or risky claims (health, finance, green claims, superlatives, "
        "targeting). Flags only, no legal advice.",
        category=AgentCategory.QUALITY,
        inputs=tuple(InputSpec(name=n, kind=k, required=False) for n, k in SAFETY_INPUTS),
        output_kind=SAFETY_KIND,
        capabilities=("claim_review", "brand_safety", "compliance_flags"),
        rubric=("accuracy",),
        max_tokens=700,
        model_class=ModelClass.SMALL,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        texts = {
            name: content_text(inputs[name].data) for name, _ in SAFETY_INPUTS if name in inputs
        }
        if not texts:
            raise AgentError(self.spec.id, "needs at least one deliverable to review")
        flags: list[SafetyFlag] = []
        for name, text in texts.items():
            flags.extend(rule_flags(name, text))
        work = "\n\n".join(f"[{name}]\n{text}" for name, text in texts.items())
        system = with_untrusted_policy(
            "You are a marketing compliance reviewer. You flag risky claims for a human to "
            "check; you do not give legal advice. Respond with one JSON object."
        )
        user = (
            f"Deliverables:\n{frame_untrusted(work[:_MAX_REVIEW_CHARS], label='deliverables')}\n\n"
            "Task: flag up to 6 risky statements a rule list might miss: unsubstantiated health, "
            "financial or environmental claims, unproven superlatives, discriminatory or "
            "exclusionary targeting. Quote each statement exactly as written and name the "
            "deliverable it is in. Return an empty list if nothing is risky.\n\n"
            'Return ONLY a JSON object: {"flags": [{"category": "health", "deliverable": '
            '"copy", "quote": "...", "note": "..."}]}'
        )
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), ModelSafetyFlags
        )
        dropped = 0
        seen = {(f.deliverable, f.quote.casefold()) for f in flags}
        for flag in result.value.flags:
            found = texts.get(flag.deliverable.strip().casefold())
            if found is None or not quoted_in(flag.quote, found):
                dropped += 1  # a flag must quote real content in a real deliverable
                continue
            key = (flag.deliverable.strip().casefold(), flag.quote.casefold())
            if key in seen:
                continue
            seen.add(key)
            flags.append(
                SafetyFlag(
                    category=flag.category,
                    deliverable=flag.deliverable.strip().casefold(),
                    quote=flag.quote,
                    source="model",
                    note=flag.note,
                )
            )
        review = SafetyReview(checked=list(texts), flags=flags, dropped_model_flags=dropped)
        artifact = Artifact.from_model(
            SAFETY_KIND,
            review,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        notes = [review.digest()]
        if dropped:
            notes.append(f"{dropped} model flag(s) dropped: their quote was not in the content")
        return AgentOutput(artifact=artifact, notes=tuple(notes))


def cro_checks(page: LandingPage) -> list[CroCheck]:
    """A conversion checklist for a landing page, computed by code."""
    placeholders = [p for p in page.proof if p.text.startswith("[")]
    return [
        CroCheck(
            name="headline_length",
            passed=len(page.headline) <= 60,
            detail=f"{len(page.headline)} characters (aim for 60 or fewer)",
        ),
        CroCheck(
            name="specific_cta",
            passed=len(page.cta_button.split()) >= 2,
            detail=f"button {page.cta_button!r} (two or more words say what happens)",
        ),
        CroCheck(
            name="benefits",
            passed=len(page.benefits) >= 3,
            detail=f"{len(page.benefits)} benefits (aim for 3 or more)",
        ),
        CroCheck(
            name="proof_supplied",
            passed=not placeholders,
            detail=f"{len(placeholders)} proof placeholder(s) still to fill"
            if placeholders
            else "proof is filled in",
        ),
        CroCheck(
            name="objections_answered",
            passed=len(page.faq) >= 3,
            detail=f"{len(page.faq)} FAQs (aim for 3 or more)",
        ),
        CroCheck(
            name="subheadline_supports",
            passed=len(page.subheadline.split()) <= 30,
            detail=f"{len(page.subheadline.split())} words (aim for 30 or fewer)",
        ),
    ]


class CroAdvisorAgent:
    spec = make_spec(
        "cro_advisor",
        "CRO advisor",
        "Conversion recommendations for a landing page: a checklist computed by code, plus "
        "prioritised changes.",
        category=AgentCategory.ANALYTICS,
        inputs=(
            InputSpec(name="landing", kind=LANDING_KIND),
            InputSpec(name="brief", kind=BRIEF_KIND, required=False),
        ),
        output_kind=CRO_KIND,
        capabilities=("conversion_optimization", "landing_page_review"),
        rubric=("actionability", "accuracy"),
        max_tokens=700,
        model_class=ModelClass.SMALL,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        page = inputs["landing"].as_model(LandingPage)
        checks = cro_checks(page)
        score = round(sum(c.passed for c in checks) / len(checks), 3)
        summary = "\n".join(
            f"- {c.name}: {'ok' if c.passed else 'FAILED'} ({c.detail})" for c in checks
        )
        system = with_untrusted_policy(
            "You are a conversion-rate optimisation consultant. You recommend specific, "
            "testable changes. Respond with one JSON object."
        )
        user = (
            f"Landing page:\n{frame_untrusted(page.to_markdown(), label='landing page')}\n\n"
            f"Automated checklist:\n{summary}\n\n"
            "Task: recommend 2-5 specific changes, each with the reason and its likely impact "
            "(high, medium or low), starting with the failed checks.\n\n"
            'Return ONLY a JSON object: {"recommendations": [{"change": "...", "reason": "...", '
            '"impact": "high"}]}'
        )
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), CroRecommendations
        )
        advice = CroAdvice(
            checks=checks, score=score, recommendations=list(result.value.recommendations)
        )
        artifact = Artifact.from_model(
            CRO_KIND,
            advice,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        return AgentOutput(artifact=artifact, notes=(advice.digest(),))
