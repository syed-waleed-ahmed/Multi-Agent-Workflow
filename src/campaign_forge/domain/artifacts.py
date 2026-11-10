"""Artifacts: the only thing agents exchange.

An artifact pairs a namespaced, versioned ``kind`` (for example
``research.audience@1``) with a JSON payload. The payload is stored as plain JSON
data, so artifacts serialise trivially into checkpoints, exports and API
responses. :meth:`Artifact.as_model` turns it back into its typed Pydantic model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .provenance import Claim
from .usage import TokenUsage

ARTIFACT_KIND_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+@[1-9][0-9]*$"

ModelT = TypeVar("ModelT", bound=BaseModel)


def _new_artifact_id() -> str:
    return f"art-{uuid.uuid4().hex[:12]}"


class ProducedBy(BaseModel):
    """Who and what produced an artifact. This is recorded so substitutions are never silent."""

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(min_length=1, max_length=100)
    agent_version: str = "0"
    prompt_version: str = "0"
    provider: str | None = None
    model: str | None = None
    fallback_from: str | None = None


class Artifact(BaseModel):
    """A typed, versioned output of one workflow step."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_new_artifact_id)
    kind: str = Field(pattern=ARTIFACT_KIND_PATTERN)
    data: dict[str, Any]
    claims: tuple[Claim, ...] = ()
    produced_by: ProducedBy
    usage: TokenUsage = TokenUsage()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_model(
        cls,
        kind: str,
        payload: BaseModel,
        *,
        produced_by: ProducedBy,
        claims: tuple[Claim, ...] = (),
        usage: TokenUsage | None = None,
    ) -> Artifact:
        """Create an artifact from a validated Pydantic payload."""
        return cls(
            kind=kind,
            data=payload.model_dump(mode="json"),
            claims=claims,
            produced_by=produced_by,
            usage=usage or TokenUsage(),
        )

    def as_model(self, model_cls: type[ModelT]) -> ModelT:
        """Validate the payload back into its typed model."""
        return model_cls.model_validate(self.data)

    @property
    def kind_name(self) -> str:
        """The kind without its version, e.g. ``research.audience``."""
        return self.kind.split("@", 1)[0]

    @property
    def kind_version(self) -> int:
        """The kind's major version, e.g. ``1``."""
        return int(self.kind.split("@", 1)[1])
