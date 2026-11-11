"""Run stores: where run records and checkpoints live.

Every operation takes a :class:`~campaign_forge.domain.scope.Scope`, so one
tenant can never read another tenant's runs.

* :class:`InMemoryRunStore`: tests, the v1 facade, and the public demo, which
  stores no visitor data on the server (ADR-008).
* :class:`JsonFileRunStore`: zero-dependency local persistence for the CLI.
  Writes are atomic (write to a temporary file, then ``os.replace``), and path
  components are encoded so ids cannot escape the root directory.

* :class:`SqliteRunStore`: one SQLite file (stdlib ``sqlite3``) that can be shared
  with the memory store (``CF_RUN_STORE=sqlite``). It passes the same contract
  tests as the other stores.

A Postgres store is deferred until it can be tested against a real database
(ADR-031).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from ..domain.scope import Scope
from ..exceptions import RunNotFoundError
from ..memory.sqlite import IN_MEMORY, connect
from .state import RUN_ID_PATTERN, RunRecord

_RUN_ID_RE = re.compile(RUN_ID_PATTERN)
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


class RunStore(Protocol):
    def save(self, record: RunRecord) -> None: ...

    def load(self, run_id: str, scope: Scope) -> RunRecord: ...

    def list_runs(self, scope: Scope) -> list[RunRecord]: ...

    def delete(self, run_id: str, scope: Scope) -> None: ...


def _check_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise RunNotFoundError(f"Invalid run id {run_id!r}.")


class InMemoryRunStore:
    """Thread-safe in-process store. Records are kept as JSON, so callers never share objects."""

    def __init__(self) -> None:
        self._data: dict[tuple[Scope, str], str] = {}
        self._lock = threading.Lock()

    def save(self, record: RunRecord) -> None:
        with self._lock:
            self._data[(record.scope, record.run_id)] = record.model_dump_json()

    def load(self, run_id: str, scope: Scope) -> RunRecord:
        with self._lock:
            raw = self._data.get((scope, run_id))
        if raw is None:
            raise RunNotFoundError(f"Run {run_id!r} not found.")
        return RunRecord.model_validate_json(raw)

    def list_runs(self, scope: Scope) -> list[RunRecord]:
        with self._lock:
            raws = [raw for (s, _), raw in self._data.items() if s == scope]
        records = [RunRecord.model_validate_json(raw) for raw in raws]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def delete(self, run_id: str, scope: Scope) -> None:
        with self._lock:
            if self._data.pop((scope, run_id), None) is None:
                raise RunNotFoundError(f"Run {run_id!r} not found.")


#: Attempts to replace a checkpoint file (D20).
REPLACE_ATTEMPTS = 8


def _replace(source: str, target: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """``os.replace`` that waits out a brief lock on Windows (D20).

    A virus scanner or the search indexer can hold a freshly written file open for
    a moment, and Windows then refuses the replace with "Access is denied". Measured
    on a live benchmark run, 2026-11-11. The replace stays atomic: it is retried,
    never emulated.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            sleep(0.05 * (attempt + 1))


def _safe_component(value: str) -> str:
    """A filesystem-safe, collision-resistant directory name for a scope id."""
    slug = _UNSAFE_RE.sub("_", value)[:40] or "_"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


class JsonFileRunStore:
    """One JSON file per run under ``root/<org>/<user>/<run_id>.json``."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def _dir(self, scope: Scope) -> Path:
        return self._root / _safe_component(scope.org_id) / _safe_component(scope.user_id)

    def _path(self, run_id: str, scope: Scope) -> Path:
        _check_run_id(run_id)
        return self._dir(scope) / f"{run_id}.json"

    def save(self, record: RunRecord) -> None:
        path = self._path(record.run_id, record.scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.model_dump_json(indent=2)
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                _replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

    def load(self, run_id: str, scope: Scope) -> RunRecord:
        path = self._path(run_id, scope)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise RunNotFoundError(f"Run {run_id!r} not found.") from None
        return RunRecord.model_validate_json(raw)

    def list_runs(self, scope: Scope) -> list[RunRecord]:
        directory = self._dir(scope)
        if not directory.is_dir():
            return []
        records = [
            RunRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in directory.glob("*.json")
            if not p.name.startswith(".tmp-")
        ]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def delete(self, run_id: str, scope: Scope) -> None:
        path = self._path(run_id, scope)
        try:
            path.unlink()
        except FileNotFoundError:
            raise RunNotFoundError(f"Run {run_id!r} not found.") from None


class SqliteRunStore:
    """One row per run in a ``runs`` table, keyed by scope and run id.

    Each operation opens and closes its own connection. Nothing is held open
    between calls, so an engine built for one CLI command never leaks a
    connection. Run writes are infrequent, so the cost is negligible.
    """

    def __init__(self, path: Path | str) -> None:
        if str(path) == IN_MEMORY:
            raise ValueError("SqliteRunStore needs a file; use InMemoryRunStore instead.")
        self._path = Path(path)
        self._lock = threading.Lock()
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "org_id TEXT NOT NULL, user_id TEXT NOT NULL, run_id TEXT NOT NULL, "
                "created_at TEXT NOT NULL, record_json TEXT NOT NULL, "
                "PRIMARY KEY (org_id, user_id, run_id))"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success, rolls back on error, and is always closed."""
        with self._lock:
            conn = connect(self._path)
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def save(self, record: RunRecord) -> None:
        _check_run_id(record.run_id)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO runs (org_id, user_id, run_id, created_at, record_json) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (org_id, user_id, run_id) "
                "DO UPDATE SET record_json = excluded.record_json",
                (
                    record.scope.org_id,
                    record.scope.user_id,
                    record.run_id,
                    record.created_at.isoformat(),
                    record.model_dump_json(),
                ),
            )

    def load(self, run_id: str, scope: Scope) -> RunRecord:
        _check_run_id(run_id)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT record_json FROM runs WHERE org_id = ? AND user_id = ? AND run_id = ?",
                (scope.org_id, scope.user_id, run_id),
            ).fetchone()
        if row is None:
            raise RunNotFoundError(f"Run {run_id!r} not found.")
        return RunRecord.model_validate_json(row["record_json"])

    def list_runs(self, scope: Scope) -> list[RunRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runs WHERE org_id = ? AND user_id = ?",
                (scope.org_id, scope.user_id),
            ).fetchall()
        records = [RunRecord.model_validate_json(row["record_json"]) for row in rows]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def delete(self, run_id: str, scope: Scope) -> None:
        _check_run_id(run_id)
        with self._connection() as conn:
            deleted = conn.execute(
                "DELETE FROM runs WHERE org_id = ? AND user_id = ? AND run_id = ?",
                (scope.org_id, scope.user_id, run_id),
            ).rowcount
        if not deleted:
            raise RunNotFoundError(f"Run {run_id!r} not found.")
