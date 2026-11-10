"""Shared helpers for orchestration tests: configurable test agents and an engine factory."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from pydantic import BaseModel

from campaign_forge.config import Settings
from campaign_forge.core import (
    AgentCategory,
    AgentLimits,
    AgentOutput,
    AgentRegistry,
    AgentSpec,
    InputSpec,
    RunContext,
)
from campaign_forge.domain import REVIEW_KIND, Artifact, ProducedBy, ReviewResult
from campaign_forge.orchestration import (
    InMemoryRunStore,
    RunStore,
    StepDefinition,
    WorkflowDefinition,
    WorkflowEngine,
)
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter

TEXT_KIND = "test.text@1"
INPUT_KIND = "test.input@1"

Behaviour = Callable[[Mapping[str, Artifact], RunContext], str]


class Text(BaseModel):
    text: str


def make_agent(
    agent_id: str,
    *,
    inputs: tuple[InputSpec, ...] = (),
    behaviour: Behaviour | None = None,
    output_kind: str = TEXT_KIND,
    limits: AgentLimits | None = None,
    version: str = "1.0.0",
    feedback: bool = False,
) -> type:
    """Build a test agent. By default it makes one model call and returns the text."""
    declared = inputs + (
        (InputSpec(name="feedback", kind=REVIEW_KIND, required=False),) if feedback else ()
    )

    class _Agent:
        spec: ClassVar[AgentSpec] = AgentSpec(
            id=agent_id,
            name=agent_id,
            description="test agent",
            category=AgentCategory.CONTENT,
            inputs=declared,
            output_kind=output_kind,
            version=version,
            limits=limits or AgentLimits(),
        )
        calls: ClassVar[list[dict[str, Artifact]]] = []

        def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
            type(self).calls.append(dict(inputs))
            if behaviour is not None:
                text = behaviour(inputs, ctx)
            else:
                request = CompletionRequest.from_prompts("system", f"run {agent_id}")
                text = ctx.complete(self.spec, request).text
            artifact = Artifact.from_model(
                output_kind,
                Text(text=text),
                produced_by=ProducedBy(agent_id=agent_id),
            )
            return AgentOutput(artifact=artifact)

    _Agent.__name__ = f"Agent_{agent_id}"
    return _Agent


def make_critic(scores: list[float]) -> type:
    """A critic returning the given scores in order (last one repeats)."""
    remaining = list(scores)

    class _Critic:
        spec: ClassVar[AgentSpec] = AgentSpec(
            id="critic",
            name="critic",
            description="test critic",
            category=AgentCategory.QUALITY,
            output_kind=REVIEW_KIND,
        )
        seen: ClassVar[list[Artifact]] = []

        def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
            type(self).seen.append(inputs["subject"])
            score = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            review = ReviewResult(score=score, summary="ok", instructions="be bolder")
            artifact = Artifact.from_model(
                REVIEW_KIND, review, produced_by=ProducedBy(agent_id="critic")
            )
            return AgentOutput(artifact=artifact)

    return _Critic


def step(step_id: str, agent: str, **kwargs: Any) -> StepDefinition:
    return StepDefinition(id=step_id, agent=agent, **kwargs)


def workflow(*steps: StepDefinition, **kwargs: Any) -> WorkflowDefinition:
    base: dict[str, Any] = {
        "id": "test-flow",
        "name": "Test flow",
        "inputs": {"topic": INPUT_KIND},
        "steps": steps,
    }
    base.update(kwargs)
    return WorkflowDefinition(**base)


def make_engine(
    *agents: type,
    provider: FakeProvider | None = None,
    store: RunStore | None = None,
    settings: Settings | None = None,
) -> tuple[WorkflowEngine, FakeProvider]:
    registry = AgentRegistry()
    for agent in agents:
        registry.register(agent)
    fake = provider or FakeProvider("ok")
    router = ProviderRouter(
        settings or Settings(_env_file=None, provider="fake"),  # type: ignore[call-arg]
        providers={"fake": fake},
    )
    return WorkflowEngine(registry, router, store=store or InMemoryRunStore()), fake


def topic(value: str = "bottles") -> dict[str, BaseModel]:
    return {"topic": Text(text=value)}


def as_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"))
