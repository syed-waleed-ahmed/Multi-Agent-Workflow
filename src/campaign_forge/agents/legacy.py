"""The four v1 agents, wrapped as registered 2.0 agents (``legacy.*``).

Each wrapper reuses the v1 agent unchanged: the same prompts, parsing and repair
logic. It swaps the v1 chat client for :class:`ContextChatClient`, so every call
goes through :meth:`RunContext.complete`, where it is budgeted, routed under
policy, cancellable and recorded. The ``classic-campaign`` workflow template
chains these four agents, and backs the v1 ``CampaignWorkflow`` API (ADR-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..core.agent import AgentCategory, AgentLimits, AgentOutput, AgentSpec, InputSpec
from ..core.context import RunContext
from ..core.registry import AgentRegistry
from ..domain.artifacts import Artifact, ProducedBy
from ..domain.usage import TokenUsage
from ..models import CampaignBrief, CopyContent
from ..providers.base import CompletionRequest, CompletionResponse, ModelClass
from .art_director import ArtDirectorAgent
from .copywriter import CopywriterAgent
from .manager import ManagerAgent
from .research import ResearchAgent
from .schemas import BRIEF_KIND

RESEARCH_KIND = "legacy.research_summary@1"
COPY_KIND = "legacy.copy@1"
PROMPTS_KIND = "legacy.image_prompts@1"
FINAL_BRIEF_KIND = "legacy.final_brief@1"


class TextPayload(BaseModel):
    """A single block of text (research summary, final brief)."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)


class PromptListPayload(BaseModel):
    """A list of image-generation prompts."""

    model_config = ConfigDict(frozen=True)

    prompts: list[str] = Field(min_length=1)


class ContextChatClient:
    """A v1 chat client whose calls go through a :class:`RunContext`."""

    def __init__(self, ctx: RunContext, spec: AgentSpec) -> None:
        self._ctx = ctx
        self._spec = spec
        self.responses: list[CompletionResponse] = []

    @property
    def model(self) -> str:
        return self.responses[-1].model if self.responses else "unknown"

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        response = self._ctx.complete(
            self._spec,
            CompletionRequest.from_prompts(system_prompt, user_prompt, json_mode=json_mode),
        )
        self.responses.append(response)
        return response.text

    def produced_by(self) -> ProducedBy:
        last = self.responses[-1] if self.responses else None
        return ProducedBy(
            agent_id=self._spec.id,
            agent_version=self._spec.version,
            prompt_version=self._spec.prompt_version,
            provider=last.provider if last else None,
            model=last.model if last else None,
            fallback_from=next((r.fallback_from for r in self.responses if r.fallback_from), None),
        )

    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for response in self.responses:
            total = total + response.usage
        return total


def _spec(
    agent_id: str,
    name: str,
    description: str,
    inputs: tuple[InputSpec, ...],
    output: str,
    *,
    max_tokens: int = 1000,
    model_class: ModelClass = ModelClass.STANDARD,
) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=name,
        description=description,
        category=AgentCategory.LEGACY,
        capabilities=("v1",),
        inputs=inputs,
        output_kind=output,
        prompt_version="v1",
        model_class=model_class,
        # The v1 agents repair internally (one extra call), plus one truncation recovery each.
        # Measured live: the v1 research answer needs ~940 completion tokens, so 800 truncated
        # it. 1,000 fits and matches Groq's free-tier per-request cap (ADR-030).
        limits=AgentLimits(max_tokens=max_tokens, max_calls=4, max_repairs=1),
    )


def _output(spec: AgentSpec, client: ContextChatClient, payload: BaseModel) -> AgentOutput:
    artifact = Artifact.from_model(
        spec.output_kind, payload, produced_by=client.produced_by(), usage=client.usage()
    )
    return AgentOutput(artifact=artifact)


_BRIEF = InputSpec(name="brief", kind=BRIEF_KIND)
_RESEARCH = InputSpec(name="research", kind=RESEARCH_KIND)
_COPY = InputSpec(name="copy", kind=COPY_KIND)
_PROMPTS = InputSpec(name="prompts", kind=PROMPTS_KIND)


class LegacyResearchAgent:
    spec: ClassVar[AgentSpec] = _spec(
        "legacy.research",
        "Research (v1)",
        "Audience, pain points, trends and positioning angles from model knowledge.",
        (_BRIEF,),
        RESEARCH_KIND,
        # Measured live (2026-11-11): the research answer can overflow 1,000 tokens even
        # when asked to be concise. 1,500 routes it past qwen3.8's per-request cap (D19).
        max_tokens=1500,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        client = ContextChatClient(ctx, self.spec)
        text = ResearchAgent(client).run(inputs["brief"].as_model(CampaignBrief))
        return _output(self.spec, client, TextPayload(text=text))


class LegacyCopywriterAgent:
    spec: ClassVar[AgentSpec] = _spec(
        "legacy.copywriter",
        "Copywriter (v1)",
        "Tagline, primary message, headlines, body copy and call to action as validated JSON.",
        (_BRIEF, _RESEARCH),
        COPY_KIND,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        client = ContextChatClient(ctx, self.spec)
        copy = CopywriterAgent(client).run(
            inputs["brief"].as_model(CampaignBrief), inputs["research"].as_model(TextPayload).text
        )
        return _output(self.spec, client, copy)


class LegacyArtDirectorAgent:
    spec: ClassVar[AgentSpec] = _spec(
        "legacy.art_director",
        "Art Director (v1)",
        "Three to five single-line image-generation prompts.",
        (_BRIEF, _RESEARCH, _COPY),
        PROMPTS_KIND,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        client = ContextChatClient(ctx, self.spec)
        prompts = ArtDirectorAgent(client).run(
            inputs["brief"].as_model(CampaignBrief),
            inputs["research"].as_model(TextPayload).text,
            inputs["copy"].as_model(CopyContent),
        )
        return _output(self.spec, client, PromptListPayload(prompts=prompts))


class LegacyManagerAgent:
    spec: ClassVar[AgentSpec] = _spec(
        "legacy.manager",
        "Campaign Manager (v1)",
        "Synthesises research, copy and visuals into the final Markdown campaign brief.",
        (_BRIEF, _RESEARCH, _COPY, _PROMPTS),
        FINAL_BRIEF_KIND,
        # Measured live: the final brief overflows 1,000 tokens even when asked to be
        # concise, and qwen3.8 is capped at 1,000 output tokens a minute on the free tier.
        # The strong class (gpt-oss-120b on Groq) returned 2,500 in one request (D17).
        max_tokens=2000,
        model_class=ModelClass.STRONG,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        client = ContextChatClient(ctx, self.spec)
        text = ManagerAgent(client).run(
            inputs["brief"].as_model(CampaignBrief),
            inputs["research"].as_model(TextPayload).text,
            inputs["copy"].as_model(CopyContent),
            inputs["prompts"].as_model(PromptListPayload).prompts,
        )
        return _output(self.spec, client, TextPayload(text=text))


LEGACY_AGENTS: tuple[type, ...] = (
    LegacyResearchAgent,
    LegacyCopywriterAgent,
    LegacyArtDirectorAgent,
    LegacyManagerAgent,
)


def register_legacy_agents(registry: AgentRegistry) -> None:
    """Register the four wrapped v1 agents."""
    for agent_cls in LEGACY_AGENTS:
        registry.register(agent_cls)
