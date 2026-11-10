"""Scoped memory: brand profiles, remembered campaigns and notes (Session 5, ADR-031).

* :class:`MemoryStore` is the contract. Every call takes a scope.
* :class:`SqliteMemoryStore` implements it with the stdlib ``sqlite3`` module:
  FTS5 search when available, a LIKE fallback otherwise, and redaction of every
  string before it is written.

A Postgres (Supabase) store is deferred until it can be tested against a real
database (docs/DECISIONS.md, ADR-031).
"""

from __future__ import annotations

from .base import (
    MAX_BRANDS_PER_SCOPE,
    MAX_DOCUMENT_CHARS,
    MAX_DOCUMENTS_PER_SCOPE,
    MEMORY_KINDS,
    MemoryDocument,
    MemoryHit,
    MemoryKind,
    MemoryStore,
)
from .sqlite import IN_MEMORY, SqliteMemoryStore, connect, fts5_available, query_tokens

__all__ = [
    "IN_MEMORY",
    "MAX_BRANDS_PER_SCOPE",
    "MAX_DOCUMENTS_PER_SCOPE",
    "MAX_DOCUMENT_CHARS",
    "MEMORY_KINDS",
    "MemoryDocument",
    "MemoryHit",
    "MemoryKind",
    "MemoryStore",
    "SqliteMemoryStore",
    "connect",
    "fts5_available",
    "query_tokens",
]
