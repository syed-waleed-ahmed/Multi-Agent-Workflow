"""Helpers for loading campaign briefs from disk."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .exceptions import ConfigurationError
from .models import CampaignBrief


def load_briefs(path: Path) -> list[CampaignBrief]:
    """Load and validate one or more campaign briefs from a JSON file.

    The file may contain either a single brief object or an array of them.

    Raises:
        ConfigurationError: if the file is missing, not valid JSON, or contains
            a brief that fails validation.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Could not read briefs file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{path} is not valid JSON: {exc}") from exc

    items = data if isinstance(data, list) else [data]
    if not items:
        raise ConfigurationError(f"{path} contains no briefs.")

    briefs: list[CampaignBrief] = []
    for index, item in enumerate(items):
        try:
            briefs.append(CampaignBrief.model_validate(item))
        except ValidationError as exc:
            raise ConfigurationError(f"Invalid brief at index {index} in {path}:\n{exc}") from exc
    return briefs
