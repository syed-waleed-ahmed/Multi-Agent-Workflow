"""User-supplied source documents (the ``input.sources@1`` workflow input).

Sources are the user's own material: notes, competitor lists, exported data, or
pages the user chose to fetch. They become evidence that research agents cite,
and are labeled ``user_provided`` in provenance.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SOURCES_KIND = "input.sources@1"
MAX_SOURCE_CHARS = 20_000
MAX_SOURCES = 10


class SourceDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SOURCE_CHARS)
    ]
    origin: str = Field(default="user", max_length=2000)


class SourceBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    documents: tuple[SourceDocument, ...] = Field(default=(), max_length=MAX_SOURCES)
