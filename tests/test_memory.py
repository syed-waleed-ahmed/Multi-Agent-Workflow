"""The SQLite memory store: tenant isolation, deletion, secret redaction and search.

Every behavioural test runs twice: with FTS5 (BM25 ranking) and with the LIKE
fallback, so both search paths keep the same contract.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from campaign_forge.domain import BrandProfile, Scope
from campaign_forge.exceptions import MemoryNotFoundError, MemoryStoreError
from campaign_forge.memory import IN_MEMORY, SqliteMemoryStore, connect, fts5_available
from campaign_forge.memory import sqlite as memory_sqlite
from campaign_forge.memory.sqlite import query_tokens
from campaign_forge.security import REDACTED

ALICE = Scope(org_id="acme", user_id="alice")
BOB = Scope(org_id="acme", user_id="bob")
OTHER_ORG = Scope(org_id="globex", user_id="alice")
CONFIGURED_SECRET = "configured-secret-value-123"


def _brand(name: str = "EcoSip", **fields: object) -> BrandProfile:
    values: dict[str, object] = {
        "name": name,
        "voice": "Plain and warm.",
        "banned_terms": ["cheap"],
        "preferred_terms": ["for years"],
    }
    values.update(fields)
    return BrandProfile.model_validate(values)


@pytest.fixture(params=[True, False], ids=["fts5", "like"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SqliteMemoryStore]:
    memory = SqliteMemoryStore(
        tmp_path / "memory.db", use_fts=request.param, secrets=[CONFIGURED_SECRET]
    )
    if request.param and not memory.full_text:
        memory.close()
        pytest.skip("this SQLite build has no FTS5")
    assert memory.full_text is request.param
    yield memory
    memory.close()


def _all_rows(path: Path) -> str:
    """Every value in every table (FTS shadow tables included), as one string."""
    conn = connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(
            str(tuple(row)) for table in tables for row in conn.execute(f'SELECT * FROM "{table}"')
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Brands
# --------------------------------------------------------------------------- #
def test_brands_are_scoped_replaced_and_deleted(store: SqliteMemoryStore) -> None:
    store.save_brand(ALICE, _brand())
    assert store.get_brand(ALICE, "  ECOSIP ").name == "EcoSip"  # looked up by slug
    with pytest.raises(MemoryNotFoundError):
        store.get_brand(BOB, "EcoSip")
    assert store.list_brands(OTHER_ORG) == []

    store.save_brand(ALICE, _brand(voice="Bold and bright."))
    brands = store.list_brands(ALICE)
    assert [b.voice for b in brands] == ["Bold and bright."]  # same name replaces

    store.save_brand(ALICE, _brand("Aqua"))
    assert [b.name for b in store.list_brands(ALICE)] == ["Aqua", "EcoSip"]
    store.delete_brand(ALICE, "EcoSip")
    with pytest.raises(MemoryNotFoundError):
        store.delete_brand(ALICE, "EcoSip")


def test_brand_limit_and_unstorable_profiles(
    store: SqliteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_sqlite, "MAX_BRANDS_PER_SCOPE", 1)
    store.save_brand(ALICE, _brand())
    store.save_brand(ALICE, _brand(voice="Updated."))  # replacing is allowed at the limit
    with pytest.raises(MemoryStoreError, match="limit"):
        store.save_brand(ALICE, _brand("Aqua"))
    # Two different secrets both redact to the same marker, so the terms would clash.
    clash = _brand("Leaky", preferred_terms=["gsk_" + "a" * 20], banned_terms=["sk-" + "b" * 20])
    with pytest.raises(MemoryStoreError, match="cannot be stored"):
        store.save_brand(BOB, clash)


# --------------------------------------------------------------------------- #
# Isolation and deletion
# --------------------------------------------------------------------------- #
def test_documents_never_cross_scopes(store: SqliteMemoryStore) -> None:
    doc = store.add_document(ALICE, "campaign", "Summer bottle launch", "EcoSip bottle summer push")
    assert [h.document.id for h in store.search(ALICE, "bottle")] == [doc.id]
    for other in (BOB, OTHER_ORG):
        assert store.search(other, "bottle") == []
        assert store.list_documents(other) == []
        with pytest.raises(MemoryNotFoundError):
            store.get_document(other, doc.id)
        with pytest.raises(MemoryNotFoundError):
            store.delete_document(other, doc.id)
    assert store.get_document(ALICE, doc.id) == doc


def test_delete_scope_removes_everything_in_that_scope_only(store: SqliteMemoryStore) -> None:
    store.save_brand(ALICE, _brand())
    store.add_document(ALICE, "campaign", "Bottle launch", "bottle campaign")
    store.add_document(ALICE, "note", "Bottle idea", "bottle note")
    kept = store.add_document(BOB, "note", "Bob's bottle", "bottle note")

    assert store.counts(ALICE) == {"brands": 1, "campaign": 1, "note": 1}
    assert store.delete_scope(ALICE) == 3
    assert store.counts(ALICE) == {"brands": 0, "campaign": 0, "note": 0}
    assert store.search(ALICE, "bottle") == []
    assert store.list_brands(ALICE) == []
    assert [h.document.id for h in store.search(BOB, "bottle")] == [kept.id]
    assert store.delete_scope(ALICE) == 0


def test_documents_are_listed_newest_first_and_deleted(store: SqliteMemoryStore) -> None:
    first = store.add_document(ALICE, "note", "First", "one")
    second = store.add_document(ALICE, "campaign", "Second", "two")
    third = store.add_document(ALICE, "note", "Third", "three")
    assert [d.id for d in store.list_documents(ALICE)] == [third.id, second.id, first.id]
    assert [d.id for d in store.list_documents(ALICE, kind="note", limit=1)] == [third.id]
    store.delete_document(ALICE, third.id)
    assert [d.id for d in store.list_documents(ALICE, kind="note")] == [first.id]
    with pytest.raises(MemoryNotFoundError):
        store.get_document(ALICE, "not-an-id")


def test_a_ref_identifies_one_document(store: SqliteMemoryStore) -> None:
    store.add_document(ALICE, "campaign", "Old title", "draft wording", ref="run-1")
    newer = store.add_document(ALICE, "campaign", "New title", "final wording", ref="run-1")
    assert [d.id for d in store.list_documents(ALICE)] == [newer.id]
    assert store.search(ALICE, "draft") == []
    assert [h.document.title for h in store.search(ALICE, "final")] == ["New title"]


def test_document_limits_and_validation(
    store: SqliteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_sqlite, "MAX_DOCUMENTS_PER_SCOPE", 2)
    store.add_document(ALICE, "note", "One", "1", ref="a")
    store.add_document(ALICE, "note", "Two", "2")
    store.add_document(ALICE, "note", "One again", "1b", ref="a")  # a replacement fits
    with pytest.raises(MemoryStoreError, match="limit"):
        store.add_document(ALICE, "note", "Three", "3")
    with pytest.raises(MemoryStoreError, match="text"):
        store.add_document(BOB, "note", "Huge", "x" * 20_001)
    with pytest.raises(MemoryStoreError, match="kind"):
        store.add_document(BOB, "diary", "Bad kind", "x")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
def test_secrets_never_reach_storage(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    leaked = ["gsk_" + "Q" * 24, "sk-" + "w" * 24, CONFIGURED_SECRET]
    with SqliteMemoryStore(path, secrets=[CONFIGURED_SECRET]) as memory:
        doc = memory.add_document(
            ALICE,
            "note",
            f"Key {leaked[0]}",
            f"Use {leaked[1]} and {leaked[2]} to log in.",
            metadata={"hint": leaked[0], "count": 3},
        )
        memory.save_brand(ALICE, _brand(voice=f"Never say {CONFIGURED_SECRET}."))
        assert REDACTED in doc.title and REDACTED in doc.text
        assert doc.metadata == {"hint": REDACTED, "count": 3}
        assert memory.search(ALICE, "log")[0].document.text.count(REDACTED) == 2
    stored = _all_rows(path)
    assert "log in" in stored  # the content itself was written...
    for secret in leaked:
        assert secret not in stored  # ...but no secret, in any table or index


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def test_search_ranks_documents_matching_more_terms_first(store: SqliteMemoryStore) -> None:
    both = store.add_document(ALICE, "campaign", "Summer bottle launch", "hydration bottle push")
    one = store.add_document(ALICE, "note", "Bottle notes", "an idea about a bottle")
    store.add_document(ALICE, "note", "Winter coat", "a warm coat")
    hits = store.search(ALICE, "hydration bottle")
    assert [h.document.id for h in hits] == [both.id, one.id]
    assert hits[0].score > hits[1].score
    assert [h.document.id for h in store.search(ALICE, "bottle", kinds=("note",))] == [one.id]
    assert len(store.search(ALICE, "bottle", limit=1)) == 1
    assert store.search(ALICE, "bottle", kinds=()) == []


def test_search_queries_cannot_inject_syntax(store: SqliteMemoryStore) -> None:
    store.add_document(ALICE, "note", "Snake case", "snake_case naming, abc letters")
    hostile = 'bottle" OR * NEAR( -- ; DROP TABLE documents; %'
    assert store.search(ALICE, hostile) == []  # reduced to plain words; nothing matches
    assert len(store.search(ALICE, "snake_case")) == 1
    assert store.search(ALICE, "a_c") == []  # "_" is literal, never a LIKE wildcard
    assert store.search(ALICE, "%") == []
    assert store.list_documents(ALICE)  # the table is intact


def test_query_tokens() -> None:
    assert query_tokens("A b, Bottle bottle!! x2") == ["bottle", "x2"]
    assert query_tokens("") == []
    assert len(query_tokens(" ".join(f"word{i}" for i in range(30)))) == 12


# --------------------------------------------------------------------------- #
# Files, schema and the FTS fallback
# --------------------------------------------------------------------------- #
def test_index_is_rebuilt_after_writes_without_fts(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with SqliteMemoryStore(path) as first:
        if not first.full_text:
            pytest.skip("this SQLite build has no FTS5")
        old = first.add_document(ALICE, "note", "Alpha", "alpha content")
    with SqliteMemoryStore(path, use_fts=False) as fallback:
        fallback.add_document(ALICE, "note", "Beta", "beta content")
        fallback.delete_document(ALICE, old.id)
    with SqliteMemoryStore(path) as reopened:
        assert reopened.full_text
        assert [h.document.title for h in reopened.search(ALICE, "beta")] == ["Beta"]
        assert reopened.search(ALICE, "alpha") == []
        conn = connect(path)
        try:
            stale = conn.execute("SELECT value FROM memory_meta WHERE key='fts_stale'").fetchall()
        finally:
            conn.close()
        assert stale == []


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    SqliteMemoryStore(path).close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("UPDATE memory_meta SET value = '99' WHERE key = 'schema_version'")
    conn.close()
    with pytest.raises(MemoryStoreError, match="schema 99"):
        SqliteMemoryStore(path)


def test_in_memory_store_lives_only_as_long_as_the_object() -> None:
    with SqliteMemoryStore(IN_MEMORY) as memory:
        memory.add_document(ALICE, "note", "Temp", "temporary")
        assert len(memory.search(ALICE, "temporary")) == 1
    with SqliteMemoryStore() as fresh:
        assert fresh.search(ALICE, "temporary") == []
    conn = connect(IN_MEMORY)
    assert isinstance(fts5_available(conn), bool)
    conn.close()
