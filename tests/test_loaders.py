"""Tests for loading briefs from disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_forge.exceptions import ConfigurationError
from campaign_forge.loaders import load_briefs

_VALID = {
    "product_name": "P",
    "product_description": "D",
    "target_audience": "A",
    "goal": "G",
    "tone": "T",
    "channels": ["email"],
}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "briefs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_single_object(tmp_path: Path) -> None:
    briefs = load_briefs(_write(tmp_path, _VALID))
    assert len(briefs) == 1
    assert briefs[0].product_name == "P"


def test_load_list(tmp_path: Path) -> None:
    briefs = load_briefs(_write(tmp_path, [_VALID, _VALID]))
    assert len(briefs) == 2


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Could not read"):
        load_briefs(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid JSON"):
        load_briefs(path)


def test_empty_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="no briefs"):
        load_briefs(_write(tmp_path, []))


def test_invalid_brief_raises_with_index(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="index 1"):
        load_briefs(_write(tmp_path, [_VALID, {"product_name": "only"}]))
