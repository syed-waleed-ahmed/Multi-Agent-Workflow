"""Domain models shared by every layer: artifacts, provenance, usage and scope.

This package imports nothing from the rest of ``campaign_forge``, which keeps
it at the bottom of the dependency graph (enforced by tests/security/test_architecture.py).
"""

from __future__ import annotations

from .artifacts import ARTIFACT_KIND_PATTERN, Artifact, ProducedBy
from .provenance import Basis, Claim, Evidence, unknown_evidence_ids
from .review import REVIEW_FEEDBACK_INPUT, REVIEW_KIND, REVIEW_SUBJECT_INPUT, ReviewResult
from .scope import Scope
from .usage import TokenUsage

__all__ = [
    "ARTIFACT_KIND_PATTERN",
    "REVIEW_FEEDBACK_INPUT",
    "REVIEW_KIND",
    "REVIEW_SUBJECT_INPUT",
    "Artifact",
    "Basis",
    "Claim",
    "Evidence",
    "ProducedBy",
    "ReviewResult",
    "Scope",
    "TokenUsage",
    "unknown_evidence_ids",
]
