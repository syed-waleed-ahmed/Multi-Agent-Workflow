"""Tests for the 2.0 domain models: usage, scope, provenance and artifacts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from campaign_forge.domain import (
    Artifact,
    Basis,
    Claim,
    Evidence,
    ProducedBy,
    Scope,
    TokenUsage,
    unknown_evidence_ids,
)


class _Payload(BaseModel):
    headline: str
    score: int


def _producer() -> ProducedBy:
    return ProducedBy(agent_id="copywriter", provider="fake", model="fake-model")


def test_token_usage_totals_and_addition() -> None:
    a = TokenUsage(prompt_tokens=10, completion_tokens=5)
    b = TokenUsage(prompt_tokens=1, completion_tokens=2)
    total = a + b
    assert total.total_tokens == 18
    assert total.complete is True


def test_unreported_usage_marks_totals_incomplete() -> None:
    total = TokenUsage(prompt_tokens=3) + TokenUsage.unreported()
    assert total.prompt_tokens == 3
    assert total.complete is False


def test_scope_validation_and_local_default() -> None:
    assert Scope.local() == Scope(org_id="local", user_id="local")
    with pytest.raises(ValidationError):
        Scope(org_id="acme corp", user_id="u1")  # spaces not allowed
    with pytest.raises(ValidationError):
        Scope(org_id="", user_id="u1")


def test_evidence_ids_are_stable_and_content_derived() -> None:
    when = datetime(2026, 11, 10, tzinfo=timezone.utc)
    first = Evidence.create(source="https://example.com", excerpt="Facts.", retrieved_at=when)
    second = Evidence.create(source="https://example.com", excerpt="Facts.", retrieved_at=when)
    other = Evidence.create(source="https://example.com", excerpt="Other facts.")
    assert first.id == second.id
    assert first.id != other.id
    assert first.id.startswith("ev-") and len(first.id) == 15
    assert len(first.content_sha256) == 64


def test_external_claim_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="must cite at least one evidence id"):
        Claim(statement="Market grew 20%.", basis=Basis.EXTERNAL_SOURCE)
    claim = Claim(statement="Audience is young.", basis=Basis.MODEL_ASSUMPTION)
    assert claim.evidence_ids == ()


def test_unknown_evidence_ids_detects_fabricated_citations() -> None:
    real = Evidence.create(source="doc.md", excerpt="Real content.")
    claims = [
        Claim(statement="Supported.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=(real.id,)),
        Claim(
            statement="Invented.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=("ev-000000000000",)
        ),
    ]
    assert unknown_evidence_ids(claims, {real.id}) == {"ev-000000000000"}
    assert unknown_evidence_ids(claims[:1], [real.id]) == set()


def test_artifact_round_trips_typed_payload() -> None:
    artifact = Artifact.from_model(
        "content.core_copy@1", _Payload(headline="Hi", score=3), produced_by=_producer()
    )
    assert artifact.id.startswith("art-")
    assert artifact.kind_name == "content.core_copy"
    assert artifact.kind_version == 1
    assert artifact.as_model(_Payload) == _Payload(headline="Hi", score=3)
    restored = Artifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact


@pytest.mark.parametrize("kind", ["research", "Research.audience@1", "research.audience", "a.b@0"])
def test_artifact_kind_must_be_namespaced_and_versioned(kind: str) -> None:
    with pytest.raises(ValidationError):
        Artifact(kind=kind, data={}, produced_by=_producer())
