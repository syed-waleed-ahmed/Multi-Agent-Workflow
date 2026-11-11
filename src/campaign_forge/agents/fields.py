"""Field types and small helpers shared by every output contract (waves 1 and 2)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import StringConstraints, ValidationInfo

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


def clip(text: str, limit: int = 160) -> str:
    """Collapse whitespace and shorten to ``limit`` characters with an ellipsis."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def context_of(info: ValidationInfo) -> dict[str, Any]:
    """The validation context as a dict (empty when none was given)."""
    return info.context if isinstance(info.context, dict) else {}
