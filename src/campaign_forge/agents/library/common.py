"""Shared machinery for the 2.0 agent library.

:class:`StructuredAgent` implements the whole agent contract once:

1. Build the prompt: framed context (brief, upstream digests), the task, any
   reviewer feedback, and the required JSON shape.
2. Call the model through ``RunContext.complete_structured``. That call is
   budgeted and routed, and it repairs malformed or rule-breaking output a
   bounded number of times.
3. Wrap the validated payload in an artifact with provenance: producing agent,
   provider and model, any fallback, token usage, and labeled claims.

A concrete agent supplies only its spec, its role, its task and its output
model. Its prompt text is its own, not a copy of another agent's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel

from ...core.agent import AgentCategory, AgentLimits, AgentOutput, AgentSpec, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact, ProducedBy
from ...domain.provenance import Claim, Evidence
from ...domain.review import REVIEW_FEEDBACK_INPUT, REVIEW_KIND, ReviewResult
from ...domain.usage import TokenUsage
from ...models import CampaignBrief
from ...providers.base import CompletionRequest, CompletionResponse, ModelClass
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ..schemas import BRIEF_KIND, MODELS_BY_KIND

FEEDBACK = InputSpec(name=REVIEW_FEEDBACK_INPUT, kind=REVIEW_KIND, required=False)
BRIEF = InputSpec(name="brief", kind=BRIEF_KIND)

JSON_ONLY = "Respond with a single valid JSON object and nothing else."


def make_spec(
    agent_id: str,
    name: str,
    description: str,
    *,
    category: AgentCategory,
    inputs: tuple[InputSpec, ...],
    output_kind: str,
    capabilities: tuple[str, ...],
    rubric: tuple[str, ...] = (),
    max_tokens: int = 900,
    model_class: ModelClass = ModelClass.STANDARD,
    reviewable: bool = False,
    prompt_version: str = "1",
    allowed_tools: tuple[str, ...] = (),
    max_calls: int = 4,
) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=name,
        description=description,
        category=category,
        capabilities=capabilities,
        inputs=inputs + ((FEEDBACK,) if reviewable else ()),
        output_kind=output_kind,
        model_class=model_class,
        allowed_tools=allowed_tools,
        limits=AgentLimits(max_tokens=max_tokens, max_calls=max_calls, max_repairs=1),
        rubric=rubric,
        prompt_version=prompt_version,
    )


def brief_of(inputs: Mapping[str, Artifact]) -> CampaignBrief:
    return inputs["brief"].as_model(CampaignBrief)


def framed_brief(inputs: Mapping[str, Artifact]) -> str:
    return frame_untrusted(brief_of(inputs).as_prompt_context(), label="campaign brief")


def digest(artifact: Artifact) -> str:
    """A compact text summary of any library artifact (falls back to its JSON keys)."""
    model = MODELS_BY_KIND.get(artifact.kind)
    if model is not None:
        value = artifact.as_model(model)
        summary = getattr(value, "digest", None)
        if callable(summary):
            return str(summary())
    return ", ".join(sorted(artifact.data))


def context(inputs: Mapping[str, Artifact], *names: str) -> str:
    """Framed digests of the named upstream artifacts that are present."""
    blocks = [
        f"{name.replace('_', ' ').title()}:\n{frame_untrusted(digest(inputs[name]), label=name)}"
        for name in names
        if name in inputs
    ]
    return "\n\n".join(blocks)


def feedback_section(inputs: Mapping[str, Artifact]) -> str:
    """Reviewer feedback (model-generated, so framed as data) and a revision instruction."""
    if REVIEW_FEEDBACK_INPUT not in inputs:
        return ""
    review = inputs[REVIEW_FEEDBACK_INPUT].as_model(ReviewResult)
    body = (
        f"Score: {review.score:.2f}\nSummary: {review.summary}\n"
        + "".join(f"- Issue: {issue}\n" for issue in review.issues)
        + (f"Instructions: {review.instructions}" if review.instructions else "")
    )
    return (
        "\n\nA reviewer assessed your previous version:\n"
        f"{frame_untrusted(body, label='reviewer feedback')}\n"
        "Write an improved version that fixes these issues."
    )


def produced_by(spec: AgentSpec, responses: tuple[CompletionResponse, ...]) -> ProducedBy:
    last = responses[-1] if responses else None
    return ProducedBy(
        agent_id=spec.id,
        agent_version=spec.version,
        prompt_version=spec.prompt_version,
        provider=last.provider if last else None,
        model=last.model if last else None,
        fallback_from=next((r.fallback_from for r in responses if r.fallback_from), None),
    )


def total_usage(responses: tuple[CompletionResponse, ...]) -> TokenUsage:
    usage = TokenUsage()
    for response in responses:
        usage = usage + response.usage
    return usage


class StructuredAgent(ABC):
    """An agent that returns one validated JSON payload."""

    spec: ClassVar[AgentSpec]
    output_model: ClassVar[type[BaseModel]]
    #: Who the agent is, as one or two sentences of system prompt.
    role: ClassVar[str]
    #: The JSON shape the model must return (a compact example).
    shape: ClassVar[str]

    @abstractmethod
    def task(self, inputs: Mapping[str, Artifact]) -> str:
        """The user prompt: framed context plus the task."""

    def notes(self, value: Any) -> tuple[str, ...]:
        """Concise, user-facing status notes about the result (never private reasoning)."""
        return ()

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        return self.produce(inputs, ctx)

    def produce(
        self,
        inputs: Mapping[str, Artifact],
        ctx: RunContext,
        *,
        extra: str = "",
        evidence: Sequence[Evidence] = (),
        validation_context: dict[str, Any] | None = None,
        notes: Sequence[str] = (),
    ) -> AgentOutput:
        """One structured call, then an artifact that embeds only the evidence it cites."""
        system = with_untrusted_policy(f"{self.role} {JSON_ONLY}")
        user = (
            f"{self.task(inputs)}{extra}{feedback_section(inputs)}\n\n"
            f"Return ONLY a JSON object of this shape:\n{self.shape}"
        )
        result = ctx.complete_structured(
            self.spec,
            CompletionRequest.from_prompts(system, user),
            self.output_model,
            validation_context=validation_context,
        )
        value = result.value
        claims_fn = getattr(value, "claims", None)
        claims: tuple[Claim, ...] = claims_fn() if callable(claims_fn) else ()
        cited = {eid for claim in claims for eid in claim.evidence_ids}
        all_notes = [*notes, *self.notes(value)]
        if result.repairs:
            all_notes.append(f"output repaired {result.repairs} time(s) to meet the contract")
        artifact = Artifact.from_model(
            self.spec.output_kind,
            value,
            produced_by=produced_by(self.spec, result.responses),
            claims=claims,
            usage=result.usage,
            evidence=tuple(e for e in evidence if e.id in cited),
        )
        return AgentOutput(artifact=artifact, notes=tuple(all_notes))


__all__ = [
    "BRIEF",
    "FEEDBACK",
    "AgentCategory",
    "InputSpec",
    "StructuredAgent",
    "brief_of",
    "context",
    "digest",
    "feedback_section",
    "framed_brief",
    "make_spec",
    "produced_by",
    "total_usage",
]
