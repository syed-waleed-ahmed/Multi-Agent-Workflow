"""The 2.0 agent contract: a declarative :class:`AgentSpec` plus a stateless ``run``.

An agent declares what it needs and what it produces as data, so the registry,
the planner, the UI and the eval harness can all reason about it without running
it. All per-run state lives in :class:`~campaign_forge.core.context.RunContext`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Annotated, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..domain.artifacts import ARTIFACT_KIND_PATTERN, Artifact
from ..providers.base import ModelClass

if TYPE_CHECKING:
    from .context import RunContext

AGENT_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"

_Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)]
_ArtifactKind = Annotated[str, StringConstraints(pattern=ARTIFACT_KIND_PATTERN)]


class AgentCategory(str, Enum):
    """Catalog categories (docs/AGENT_CATALOG.md)."""

    RESEARCH = "research"
    STRATEGY = "strategy"
    CONTENT = "content"
    SEO = "seo"
    CREATIVE = "creative"
    ANALYTICS = "analytics"
    QUALITY = "quality"
    SYNTHESIS = "synthesis"
    LEGACY = "legacy"


class AgentLimits(BaseModel):
    """Per-agent execution limits, enforced by the engine and the run context."""

    model_config = ConfigDict(frozen=True)

    max_tokens: int = Field(default=800, gt=0, le=32_000)
    max_calls: int = Field(default=3, ge=1, le=20)
    # Generous: waiting out a free-tier rate limit (about 45 s per hit) is normal, not a hang.
    timeout_s: float = Field(default=900.0, gt=0.0, le=3600.0)
    max_repairs: int = Field(default=1, ge=0, le=3)


class InputSpec(BaseModel):
    """One named input an agent consumes."""

    model_config = ConfigDict(frozen=True)

    name: _Identifier
    kind: _ArtifactKind
    required: bool = True


class AgentSpec(BaseModel):
    """Everything the platform needs to know about an agent without running it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=AGENT_ID_PATTERN, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    prompt_version: str = Field(default="1", min_length=1, max_length=20)
    category: AgentCategory
    capabilities: tuple[str, ...] = ()
    inputs: tuple[InputSpec, ...] = ()
    output_kind: _ArtifactKind
    allowed_tools: tuple[str, ...] = ()
    model_class: ModelClass = ModelClass.STANDARD
    provider: str | None = None
    limits: AgentLimits = AgentLimits()
    rubric: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_input_names(self) -> AgentSpec:
        names = [item.name for item in self.inputs]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"Duplicate input names: {', '.join(duplicates)}")
        return self

    @property
    def required_inputs(self) -> tuple[InputSpec, ...]:
        return tuple(item for item in self.inputs if item.required)


class AgentOutput(BaseModel):
    """What an agent returns: one artifact plus concise, user-facing status notes.

    ``notes`` summarise decisions ("2 claims downgraded to assumptions"). They are
    not private reasoning and must never contain chain-of-thought.
    """

    model_config = ConfigDict(frozen=True)

    artifact: Artifact
    notes: tuple[str, ...] = ()


@runtime_checkable
class Agent(Protocol):
    """A stateless agent. Instances are created per step by the registry."""

    spec: ClassVar[AgentSpec]

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput: ...
