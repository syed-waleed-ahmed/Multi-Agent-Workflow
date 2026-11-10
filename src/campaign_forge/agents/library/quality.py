"""Quality agents (wave 1): the critic that drives the engine's bounded review loop.

The critic combines two layers:

1. **Deterministic checks** (:mod:`campaign_forge.evaluation.checks`): schema,
   injection echoes, grounding, labeled claims, platform limits. They are free
   and reproducible. Any critical failure caps the score below every sensible
   threshold, so the producing agent is asked to revise.
2. **A model judge** that scores the kind's rubric criteria from 1 to 5. Code
   converts those scores to a 0-1 score (ADR-011).

The review is returned as ``quality.review@1`` (:class:`ReviewResult`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.provenance import Basis, Evidence
from ...domain.review import REVIEW_KIND, REVIEW_SUBJECT_INPUT, ReviewResult
from ...evaluation.checks import check_artifact, critical_failures, rubric_for
from ...models import CampaignBrief
from ...providers.base import CompletionRequest, ModelClass
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import (
    AUDIENCE_KIND,
    BRIEF_KIND,
    COMPETITORS_KIND,
    FACT_CHECK_KIND,
    MARKET_KIND,
    CheckedClaim,
    FactCheck,
    FactCheckVerdicts,
)
from .common import OPTIONAL_BRAND, AgentCategory, digest, framed_brand, make_spec, produced_by

#: Score ceiling when a deterministic critical check fails.
CRITICAL_FAILURE_CAP = 0.4


class JudgeVerdict(BaseModel):
    """What the model judge returns."""

    scores: dict[str, int]
    issues: list[str] = Field(default_factory=list, max_length=8)
    instructions: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=1000)

    @field_validator("scores")
    @classmethod
    def _in_range(cls, value: dict[str, int]) -> dict[str, int]:
        bad = {k: v for k, v in value.items() if not 1 <= v <= 5}
        if bad:
            raise ValueError(f"scores must be integers from 1 to 5; got {bad}")
        return value


def rubric_score(scores: Mapping[str, int], criteria: tuple[str, ...]) -> float:
    """Map 1-5 rubric scores to 0-1. A criterion the judge skipped scores 1 (worst)."""
    values = [scores.get(c, 1) for c in criteria]
    return round((sum(values) / len(values) - 1) / 4, 3)


class CriticAgent:
    spec = make_spec(
        "critic",
        "Critic",
        "Reviews any artifact against deterministic checks and a kind-specific rubric.",
        category=AgentCategory.QUALITY,
        inputs=(InputSpec(name="brief", kind=BRIEF_KIND, required=False), OPTIONAL_BRAND),
        output_kind=REVIEW_KIND,
        capabilities=("review", "quality_assurance"),
        rubric=("calibration",),
        max_tokens=500,
        model_class=ModelClass.SMALL,
    )
    role: ClassVar[str] = (
        "You are a demanding but fair creative director reviewing marketing work. You "
        "score against the rubric and give specific, actionable fixes."
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        subject = inputs[REVIEW_SUBJECT_INPUT]
        brief = inputs["brief"].as_model(CampaignBrief) if "brief" in inputs else None
        checks = check_artifact(subject, brief)
        failures = critical_failures(checks)
        criteria = rubric_for(subject.kind)
        brief_block = (
            "Campaign brief:\n"
            f"{frame_untrusted(brief.as_prompt_context(), label='campaign brief')}\n\n"
            if brief
            else ""
        )
        automated = (
            "\n".join(
                f"- {c.name}: {'FAILED' if not c.passed else 'ok'} {c.detail}" for c in checks
            )
            or "- none"
        )
        user = (
            f"{brief_block}{framed_brand(inputs)}Work under review ({subject.kind}):\n"
            f"{frame_untrusted(_render(subject), label='work under review')}\n\n"
            f"Automated checks:\n{automated}\n\n"
            f"Score each criterion from 1 (poor) to 5 (excellent): {', '.join(criteria)}. "
            "List the most important issues and one paragraph of revision instructions.\n\n"
            'Return ONLY a JSON object: {"scores": {"criterion": 3}, "issues": ["..."], '
            '"instructions": "...", "summary": "..."}'
        )
        system = with_untrusted_policy(f"{self.role} Respond with a single valid JSON object.")
        result = ctx.complete_structured(
            self.spec, CompletionRequest.from_prompts(system, user), JudgeVerdict
        )
        verdict = result.value
        judged = rubric_score(verdict.scores, criteria)
        score = min(judged, CRITICAL_FAILURE_CAP) if failures else judged
        issues = [f"{c.name}: {c.detail}" for c in failures] + list(verdict.issues)
        review = ReviewResult(
            score=score,
            summary=verdict.summary or f"Rubric score {judged:.2f}.",
            issues=tuple(issues[:10]),
            instructions=verdict.instructions,
        )
        notes = [f"rubric {judged:.2f}; {len(failures)} critical check failure(s)"]
        artifact = Artifact.from_model(
            REVIEW_KIND,
            review,
            produced_by=produced_by(self.spec, result.responses),
            usage=result.usage,
        )
        return AgentOutput(artifact=artifact, notes=tuple(notes))


def _render(artifact: Artifact) -> str:
    """The reviewed work: its digest plus its full Markdown when the model offers it."""
    from ..schemas import MODELS_BY_KIND

    model = MODELS_BY_KIND.get(artifact.kind)
    if model is not None:
        value: Any = artifact.as_model(model)
        to_markdown = getattr(value, "to_markdown", None)
        if callable(to_markdown):
            return str(to_markdown())[:4000]
    return digest(artifact)[:4000]


_CHECKABLE = (
    InputSpec(name="competitors", kind=COMPETITORS_KIND, required=False),
    InputSpec(name="market", kind=MARKET_KIND, required=False),
    InputSpec(name="audience", kind=AUDIENCE_KIND, required=False),
)


class FactCheckerAgent:
    """Checks every externally sourced claim against the evidence it cites.

    The model only returns a verdict per numbered claim. Code maps the verdicts back
    to the claims, so the model cannot rewrite the claims it judges. The counts are
    computed by code. With no external claims, no model call is made.
    """

    spec = make_spec(
        "fact_checker",
        "Fact checker",
        "Verifies cited claims against their evidence: supported, partly, or unsupported.",
        category=AgentCategory.QUALITY,
        inputs=_CHECKABLE,
        output_kind=FACT_CHECK_KIND,
        capabilities=("fact_checking", "verification"),
        rubric=("accuracy",),
        max_tokens=900,
        model_class=ModelClass.SMALL,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        claims: list[tuple[str, tuple[str, ...]]] = []
        evidence: dict[str, Evidence] = {}
        for item in _CHECKABLE:
            artifact = inputs.get(item.name)
            if artifact is None:
                continue
            evidence.update({e.id: e for e in artifact.evidence})
            for claim in artifact.claims:
                entry = (claim.statement, claim.evidence_ids)
                if claim.basis is Basis.EXTERNAL_SOURCE and entry not in claims:
                    claims.append(entry)
        if not claims:
            result = FactCheck(claims=[])
            artifact = Artifact.from_model(
                FACT_CHECK_KIND, result, produced_by=produced_by(self.spec, ())
            )
            return AgentOutput(
                artifact=artifact, notes=("nothing to check: no externally sourced claims",)
            )

        blocks = []
        for index, (statement, ids) in enumerate(claims):
            cited = (
                "\n".join(
                    f"[{eid}] {' '.join(evidence[eid].excerpt.split())[:500]}"
                    for eid in ids
                    if eid in evidence
                )
                or "(cited evidence is missing)"
            )
            blocks.append(f"Claim {index}: {statement}\nEvidence:\n{cited}")
        system = with_untrusted_policy(
            "You are a meticulous fact checker. You judge only whether the quoted evidence "
            "supports each claim, without using outside knowledge. Respond with one JSON object."
        )
        user = (
            f"{frame_untrusted(chr(10).join(blocks), label='claims and evidence')}\n\n"
            "For every claim, decide whether its evidence fully supports it (supported), "
            "partly supports it (partially_supported), or does not support it (unsupported).\n\n"
            'Return ONLY a JSON object: {"verdicts": [{"index": 0, "verdict": "supported", '
            '"note": "..."}]}'
        )
        response = ctx.complete_structured(
            self.spec,
            CompletionRequest.from_prompts(system, user),
            FactCheckVerdicts,
            validation_context={"claim_count": len(claims)},
        )
        by_index = {v.index: v for v in response.value.verdicts}
        checked = [
            CheckedClaim(
                statement=statement,
                evidence_ids=list(ids),
                verdict=by_index[i].verdict if i in by_index else "not_checked",
                note=by_index[i].note if i in by_index else "",
            )
            for i, (statement, ids) in enumerate(claims)
        ]
        result = FactCheck(claims=checked)
        cited_ids = {eid for _, ids in claims for eid in ids}
        artifact = Artifact.from_model(
            FACT_CHECK_KIND,
            result,
            produced_by=produced_by(self.spec, response.responses),
            usage=response.usage,
            evidence=tuple(e for eid, e in evidence.items() if eid in cited_ids),
        )
        return AgentOutput(artifact=artifact, notes=(result.digest(),))
