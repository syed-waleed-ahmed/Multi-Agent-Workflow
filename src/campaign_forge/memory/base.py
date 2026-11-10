"""The memory contract: scoped brand profiles and searchable documents.

Every method takes a :class:`~campaign_forge.domain.scope.Scope`. There is no
method that reads across scopes, so one tenant can never see another tenant's
memory, and :meth:`MemoryStore.delete_scope` removes everything a tenant stored.

Memory is **data, never instructions**. Whatever is retrieved from it reaches a
prompt only inside untrusted-data framing (docs/SECURITY.md, T2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..domain.brand import BrandProfile
from ..domain.scope import Scope

MemoryKind = Literal["campaign", "note"]
MEMORY_KINDS: tuple[MemoryKind, ...] = ("campaign", "note")

MAX_TITLE_CHARS = 300
MAX_DOCUMENT_CHARS = 20_000
MAX_DOCUMENTS_PER_SCOPE = 2_000
MAX_BRANDS_PER_SCOPE = 100
MAX_SEARCH_RESULTS = 20

MetadataValue = str | int | float | bool | None


class MemoryDocument(BaseModel):
    """A stored, searchable piece of memory: a remembered campaign or a user note."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    scope: Scope
    kind: MemoryKind
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)
    ]
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DOCUMENT_CHARS)
    ]
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    #: An external reference (e.g. the run id of a remembered campaign). At most one
    #: document per scope, kind and ref exists; saving again replaces it.
    ref: str | None = Field(default=None, max_length=200)
    created_at: datetime


class MemoryHit(BaseModel):
    """A search result. A higher score is a better match; scores compare within one search."""

    model_config = ConfigDict(frozen=True)

    document: MemoryDocument
    score: float


class MemoryStore(Protocol):
    def save_brand(self, scope: Scope, profile: BrandProfile) -> BrandProfile: ...

    def get_brand(self, scope: Scope, name: str) -> BrandProfile: ...

    def list_brands(self, scope: Scope) -> list[BrandProfile]: ...

    def delete_brand(self, scope: Scope, name: str) -> None: ...

    def add_document(
        self,
        scope: Scope,
        kind: MemoryKind,
        title: str,
        text: str,
        *,
        metadata: dict[str, MetadataValue] | None = None,
        ref: str | None = None,
    ) -> MemoryDocument: ...

    def get_document(self, scope: Scope, doc_id: str) -> MemoryDocument: ...

    def list_documents(
        self, scope: Scope, *, kind: MemoryKind | None = None, limit: int = 50
    ) -> list[MemoryDocument]: ...

    def delete_document(self, scope: Scope, doc_id: str) -> None: ...

    def search(
        self,
        scope: Scope,
        query: str,
        *,
        kinds: tuple[MemoryKind, ...] = MEMORY_KINDS,
        limit: int = 5,
    ) -> list[MemoryHit]: ...

    def counts(self, scope: Scope) -> dict[str, int]: ...

    def delete_scope(self, scope: Scope) -> int: ...
