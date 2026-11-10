"""Provenance: where a claim comes from, and the evidence that backs it.

Two rules make fabricated research detectable:

1. Only tools create :class:`Evidence` records. Models never do.
2. A :class:`Claim` whose basis is :attr:`Basis.EXTERNAL_SOURCE` must cite at
   least one evidence id. :func:`unknown_evidence_ids` then checks that every
   cited id really exists in the run's evidence store.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

EVIDENCE_ID_PATTERN = r"^ev-[0-9a-f]{12}$"

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Basis(str, Enum):
    """The basis on which a claim is made."""

    USER_PROVIDED = "user_provided"
    EXTERNAL_SOURCE = "external_source"
    STORED_MEMORY = "stored_memory"
    MODEL_ASSUMPTION = "model_assumption"
    DERIVED = "derived"


class Evidence(BaseModel):
    """A piece of source material retrieved by a tool."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    source: _Text = Field(max_length=2000)
    title: str = Field(default="", max_length=500)
    excerpt: _Text = Field(max_length=8000)
    retrieved_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        source: str,
        excerpt: str,
        title: str = "",
        retrieved_at: datetime | None = None,
    ) -> Evidence:
        """Build an evidence record with a stable id derived from its source and content."""
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        evidence_id = hashlib.sha256(f"{source}\n{digest}".encode()).hexdigest()[:12]
        return cls(
            id=f"ev-{evidence_id}",
            source=source,
            title=title,
            excerpt=excerpt,
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
            content_sha256=digest,
        )


class Claim(BaseModel):
    """A single statement with an explicit basis and optional supporting evidence."""

    model_config = ConfigDict(frozen=True)

    statement: _Text = Field(max_length=2000)
    basis: Basis
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _external_claims_cite_evidence(self) -> Claim:
        if self.basis is Basis.EXTERNAL_SOURCE and not self.evidence_ids:
            raise ValueError(
                "A claim based on an external source must cite at least one evidence id."
            )
        return self


def unknown_evidence_ids(claims: Iterable[Claim], known_ids: Collection[str]) -> set[str]:
    """Return every cited evidence id that is not in ``known_ids``.

    An empty result means every citation points at evidence a tool actually retrieved.
    """
    known = set(known_ids)
    return {eid for claim in claims for eid in claim.evidence_ids if eid not in known}
