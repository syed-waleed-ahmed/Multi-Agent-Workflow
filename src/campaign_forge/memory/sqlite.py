"""SQLite memory store: stdlib ``sqlite3``, no server and no extra dependency (ADR-031).

* **Scope on every call.** Every query filters on the scope's ``org_id`` and
  ``user_id``. :meth:`SqliteMemoryStore.delete_scope` removes a tenant's brands,
  documents and search index entries in one transaction.
* **Secrets never reach storage.** Every string written (brand fields, titles,
  text, metadata values) is redacted first: configured secret values and
  key-shaped strings become ``[REDACTED]``.
* **Search.** FTS5 with BM25 ranking (title weighted above body) when the SQLite
  build includes FTS5, otherwise a LIKE scan (substring matches, so "or" also
  matches "words") ranked by term counts. If documents
  are written while FTS5 is unavailable, the index is marked stale and rebuilt
  the next time the database is opened with FTS5.
* **Concurrency.** One connection per store, guarded by a lock. File databases
  use WAL, so the CLI and a run store on the same file do not block readers.

SQL text never contains user data: every value is a bound parameter, and search
queries are reduced to quoted word tokens before they reach FTS5.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import ValidationError

from ..domain.brand import BrandProfile, brand_slug
from ..domain.scope import Scope
from ..exceptions import MemoryNotFoundError, MemoryStoreError
from ..security.redaction import redact
from .base import (
    MAX_BRANDS_PER_SCOPE,
    MAX_DOCUMENTS_PER_SCOPE,
    MAX_SEARCH_RESULTS,
    MEMORY_KINDS,
    MemoryDocument,
    MemoryHit,
    MemoryKind,
    MetadataValue,
)

IN_MEMORY = ":memory:"
SCHEMA_VERSION = 1
MAX_QUERY_TOKENS = 12
_LIKE_CANDIDATES = 500

_TOKEN_RE = re.compile(r"\w+")
_DOC_ID_RE = re.compile(r"^mem-[0-9a-f]{12}$")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS brands (
        org_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (org_id, user_id, slug)
    )""",
    """CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        ref TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS documents_by_scope "
    "ON documents (org_id, user_id, kind, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS documents_by_ref ON documents (org_id, user_id, kind, ref) "
    "WHERE ref IS NOT NULL",
)
_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
    "doc_id UNINDEXED, org_id UNINDEXED, user_id UNINDEXED, kind UNINDEXED, title, body, "
    "tokenize = 'unicode61 remove_diacritics 2')"
)
#: BM25 column weights, in column order: the four UNINDEXED columns, title, body.
_BM25 = "bm25(documents_fts, 0.0, 0.0, 0.0, 0.0, 2.0, 1.0)"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite database with the package defaults (shared with the SQLite run store)."""
    target = str(path)
    if target != IN_MEMORY:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    if target != IN_MEMORY:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Whether this SQLite build can create FTS5 tables."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.cf_fts_probe USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    conn.execute("DROP TABLE temp.cf_fts_probe")
    return True


def query_tokens(query: str) -> list[str]:
    """The search words in ``query``: lower-case, de-duplicated, at most 12, two or more chars."""
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(query.casefold()):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens[:MAX_QUERY_TOKENS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _like(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SqliteMemoryStore:
    """Brand profiles and searchable documents in one SQLite file (or in memory).

    Args:
        path: A database file, or ``":memory:"`` for a store that lives only as
            long as this object (tests and the public demo).
        secrets: Configured secret values to redact, in addition to key-shaped strings.
        use_fts: ``False`` forces the LIKE fallback (for tests); ``None`` detects FTS5.
    """

    def __init__(
        self,
        path: Path | str = IN_MEMORY,
        *,
        secrets: Iterable[str] = (),
        use_fts: bool | None = None,
    ) -> None:
        self._conn = connect(path)
        self._lock = threading.Lock()
        self._secrets = tuple(secrets)
        self._fts = use_fts is not False and fts5_available(self._conn)
        with self._tx() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            version = self._meta(conn, "schema_version")
            if version is None:
                self._set_meta(conn, "schema_version", str(SCHEMA_VERSION))
            elif int(version) > SCHEMA_VERSION:
                raise MemoryStoreError(
                    f"The memory database uses schema {version}; this version supports "
                    f"{SCHEMA_VERSION}. Upgrade Campaign Forge to open it."
                )
            if self._fts:
                conn.execute(_FTS_SCHEMA)
                if self._meta(conn, "fts_stale") is not None:
                    self._rebuild_index(conn)

    @property
    def full_text(self) -> bool:
        """``True`` when searches use FTS5 (BM25 ranking), ``False`` for the LIKE fallback."""
        return self._fts

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteMemoryStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Brands
    # ------------------------------------------------------------------ #
    def save_brand(self, scope: Scope, profile: BrandProfile) -> BrandProfile:
        """Create or replace the brand with this name. Returns the profile as stored."""
        try:
            clean = BrandProfile.model_validate(self._clean(profile.model_dump(mode="json")))
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
            raise MemoryStoreError(f"Brand profile cannot be stored: {message}") from exc
        now = _now()
        with self._tx() as conn:
            exists = conn.execute(
                "SELECT 1 FROM brands WHERE org_id = ? AND user_id = ? AND slug = ?",
                (scope.org_id, scope.user_id, clean.slug),
            ).fetchone()
            if exists is None:
                (count,) = conn.execute(
                    "SELECT count(*) FROM brands WHERE org_id = ? AND user_id = ?",
                    (scope.org_id, scope.user_id),
                ).fetchone()
                if count >= MAX_BRANDS_PER_SCOPE:
                    raise MemoryStoreError(
                        f"This scope already stores {count} brands (the limit); delete one first."
                    )
            conn.execute(
                "INSERT INTO brands (org_id, user_id, slug, name, profile_json, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (org_id, user_id, slug) DO UPDATE SET name = excluded.name, "
                "profile_json = excluded.profile_json, updated_at = excluded.updated_at",
                (
                    scope.org_id,
                    scope.user_id,
                    clean.slug,
                    clean.name,
                    clean.model_dump_json(),
                    now,
                    now,
                ),
            )
        return clean

    def get_brand(self, scope: Scope, name: str) -> BrandProfile:
        with self._lock:
            row = self._conn.execute(
                "SELECT profile_json FROM brands WHERE org_id = ? AND user_id = ? AND slug = ?",
                (scope.org_id, scope.user_id, brand_slug(name)),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError(f"Brand {name!r} not found.")
        return BrandProfile.model_validate_json(row["profile_json"])

    def list_brands(self, scope: Scope) -> list[BrandProfile]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT profile_json FROM brands WHERE org_id = ? AND user_id = ? "
                "ORDER BY name COLLATE NOCASE",
                (scope.org_id, scope.user_id),
            ).fetchall()
        return [BrandProfile.model_validate_json(row["profile_json"]) for row in rows]

    def delete_brand(self, scope: Scope, name: str) -> None:
        with self._tx() as conn:
            deleted = conn.execute(
                "DELETE FROM brands WHERE org_id = ? AND user_id = ? AND slug = ?",
                (scope.org_id, scope.user_id, brand_slug(name)),
            ).rowcount
        if not deleted:
            raise MemoryNotFoundError(f"Brand {name!r} not found.")

    # ------------------------------------------------------------------ #
    # Documents
    # ------------------------------------------------------------------ #
    def add_document(
        self,
        scope: Scope,
        kind: MemoryKind,
        title: str,
        text: str,
        *,
        metadata: dict[str, MetadataValue] | None = None,
        ref: str | None = None,
    ) -> MemoryDocument:
        """Store a document. A document with the same scope, kind and ``ref`` is replaced."""
        try:
            document = MemoryDocument(
                id=f"mem-{uuid.uuid4().hex[:12]}",
                scope=scope,
                kind=kind,
                title=self._clean(title),
                text=self._clean(text),
                metadata=self._clean(dict(metadata or {})),
                ref=ref,
                created_at=datetime.now(timezone.utc),
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(p) for p in error["loc"]) or "document"
            raise MemoryStoreError(f"Memory document rejected ({where}): {error['msg']}") from exc
        with self._tx() as conn:
            previous = None
            if ref is not None:
                previous = conn.execute(
                    "SELECT id FROM documents WHERE org_id = ? AND user_id = ? AND kind = ? "
                    "AND ref = ?",
                    (scope.org_id, scope.user_id, kind, ref),
                ).fetchone()
            if previous is not None:
                self._delete_documents(conn, [previous["id"]])
            else:
                (count,) = conn.execute(
                    "SELECT count(*) FROM documents WHERE org_id = ? AND user_id = ?",
                    (scope.org_id, scope.user_id),
                ).fetchone()
                if count >= MAX_DOCUMENTS_PER_SCOPE:
                    raise MemoryStoreError(
                        f"This scope already stores {count} documents (the limit); delete some "
                        "or run 'memory forget'."
                    )
            conn.execute(
                "INSERT INTO documents (id, org_id, user_id, kind, title, body, metadata_json, "
                "ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document.id,
                    scope.org_id,
                    scope.user_id,
                    document.kind,
                    document.title,
                    document.text,
                    json.dumps(document.metadata, sort_keys=True),
                    document.ref,
                    document.created_at.isoformat(timespec="microseconds"),
                ),
            )
            if self._fts:
                conn.execute(
                    "INSERT INTO documents_fts (doc_id, org_id, user_id, kind, title, body) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        document.id,
                        scope.org_id,
                        scope.user_id,
                        document.kind,
                        document.title,
                        document.text,
                    ),
                )
            else:
                self._set_meta(conn, "fts_stale", "1")
        return document

    def get_document(self, scope: Scope, doc_id: str) -> MemoryDocument:
        if not _DOC_ID_RE.match(doc_id):
            raise MemoryNotFoundError(f"Memory document {doc_id!r} not found.")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id = ? AND org_id = ? AND user_id = ?",
                (doc_id, scope.org_id, scope.user_id),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError(f"Memory document {doc_id!r} not found.")
        return _document(row)

    def list_documents(
        self, scope: Scope, *, kind: MemoryKind | None = None, limit: int = 50
    ) -> list[MemoryDocument]:
        kinds = (kind,) if kind else MEMORY_KINDS
        marks = ", ".join("?" for _ in kinds)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE org_id = ? AND user_id = ? "
                f"AND kind IN ({marks}) ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (scope.org_id, scope.user_id, *kinds, max(1, min(limit, 500))),
            ).fetchall()
        return [_document(row) for row in rows]

    def delete_document(self, scope: Scope, doc_id: str) -> None:
        self.get_document(scope, doc_id)
        with self._tx() as conn:
            self._delete_documents(conn, [doc_id])

    def counts(self, scope: Scope) -> dict[str, int]:
        """How many brands and documents of each kind this scope stores."""
        with self._lock:
            (brands,) = self._conn.execute(
                "SELECT count(*) FROM brands WHERE org_id = ? AND user_id = ?",
                (scope.org_id, scope.user_id),
            ).fetchone()
            rows = self._conn.execute(
                "SELECT kind, count(*) AS n FROM documents WHERE org_id = ? AND user_id = ? "
                "GROUP BY kind",
                (scope.org_id, scope.user_id),
            ).fetchall()
        result: dict[str, int] = {"brands": int(brands), **dict.fromkeys(MEMORY_KINDS, 0)}
        result.update({row["kind"]: int(row["n"]) for row in rows})
        return result

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def search(
        self,
        scope: Scope,
        query: str,
        *,
        kinds: tuple[MemoryKind, ...] = MEMORY_KINDS,
        limit: int = 5,
    ) -> list[MemoryHit]:
        """Documents in this scope that match any word of ``query``, best match first."""
        tokens = query_tokens(query)
        kinds = tuple(k for k in kinds if k in MEMORY_KINDS)
        if not tokens or not kinds:
            return []
        limit = max(1, min(limit, MAX_SEARCH_RESULTS))
        with self._lock:
            if self._fts:
                return self._search_fts(scope, tokens, kinds, limit)
            return self._search_like(scope, tokens, kinds, limit)

    def _search_fts(
        self, scope: Scope, tokens: Sequence[str], kinds: Sequence[str], limit: int
    ) -> list[MemoryHit]:
        match = " OR ".join(f'"{token}"' for token in tokens)
        marks = ", ".join("?" for _ in kinds)
        rows = self._conn.execute(
            f"SELECT d.*, -{_BM25} AS score FROM documents_fts "
            "JOIN documents AS d ON d.id = documents_fts.doc_id "
            "WHERE documents_fts MATCH ? AND documents_fts.org_id = ? "
            "AND documents_fts.user_id = ? AND d.org_id = ? AND d.user_id = ? "
            f"AND d.kind IN ({marks}) "
            "ORDER BY score DESC, d.created_at DESC LIMIT ?",
            (
                match,
                scope.org_id,
                scope.user_id,
                scope.org_id,
                scope.user_id,
                *kinds,
                limit,
            ),
        ).fetchall()
        return [MemoryHit(document=_document(row), score=round(row["score"], 6)) for row in rows]

    def _search_like(
        self, scope: Scope, tokens: Sequence[str], kinds: Sequence[str], limit: int
    ) -> list[MemoryHit]:
        clause = "title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'"
        any_token = " OR ".join(clause for _ in tokens)
        marks = ", ".join("?" for _ in kinds)
        patterns = [p for token in tokens for p in (_like(token), _like(token))]
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE org_id = ? AND user_id = ? "
            f"AND kind IN ({marks}) AND ({any_token}) "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (scope.org_id, scope.user_id, *kinds, *patterns, _LIKE_CANDIDATES),
        ).fetchall()
        scored = []
        for position, row in enumerate(rows):
            title, body = row["title"].casefold(), row["body"].casefold()
            score = sum(2 * title.count(token) + body.count(token) for token in tokens)
            scored.append((score, -position, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            MemoryHit(document=_document(row), score=float(score))
            for score, _, row in scored[:limit]
        ]

    # ------------------------------------------------------------------ #
    # Deletion
    # ------------------------------------------------------------------ #
    def delete_scope(self, scope: Scope) -> int:
        """Delete every brand and document in ``scope``. Returns how many records were removed."""
        with self._tx() as conn:
            params = (scope.org_id, scope.user_id)
            if self._fts:
                conn.execute("DELETE FROM documents_fts WHERE org_id = ? AND user_id = ?", params)
            else:
                self._set_meta(conn, "fts_stale", "1")
            documents = conn.execute(
                "DELETE FROM documents WHERE org_id = ? AND user_id = ?", params
            ).rowcount
            brands = conn.execute(
                "DELETE FROM brands WHERE org_id = ? AND user_id = ?", params
            ).rowcount
        return int(documents) + int(brands)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _clean(self, value: Any) -> Any:
        """Redact secrets from every string in ``value`` (recursively)."""
        if isinstance(value, str):
            return redact(value, self._secrets)
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        return value

    def _delete_documents(self, conn: sqlite3.Connection, ids: Sequence[str]) -> None:
        if self._fts:
            conn.executemany("DELETE FROM documents_fts WHERE doc_id = ?", [(i,) for i in ids])
        else:
            self._set_meta(conn, "fts_stale", "1")
        conn.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i in ids])

    @staticmethod
    def _rebuild_index(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM documents_fts")
        conn.execute(
            "INSERT INTO documents_fts (doc_id, org_id, user_id, kind, title, body) "
            "SELECT id, org_id, user_id, kind, title, body FROM documents"
        )
        conn.execute("DELETE FROM memory_meta WHERE key = 'fts_stale'")

    @staticmethod
    def _meta(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM memory_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO memory_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _document(row: sqlite3.Row) -> MemoryDocument:
    return MemoryDocument(
        id=row["id"],
        scope=Scope(org_id=row["org_id"], user_id=row["user_id"]),
        kind=row["kind"],
        title=row["title"],
        text=row["body"],
        metadata=json.loads(row["metadata_json"]),
        ref=row["ref"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
