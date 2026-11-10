"""The review contract shared by critic agents and the orchestration engine.

A critic receives the artifact under review as its ``subject`` input and returns
a ``quality.review@1`` artifact whose payload is a :class:`ReviewResult`. When the
score is below the step's threshold, the engine re-runs the producing agent with
the review as its optional ``feedback`` input. It does this at most
``max_revisions`` times.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

REVIEW_KIND = "quality.review@1"
REVIEW_SUBJECT_INPUT = "subject"
REVIEW_FEEDBACK_INPUT = "feedback"


class ReviewResult(BaseModel):
    """A critic's verdict on one artifact."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=2000)
    issues: tuple[str, ...] = ()
    instructions: str = Field(default="", max_length=4000)
