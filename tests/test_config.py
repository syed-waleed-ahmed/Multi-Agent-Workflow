"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from campaign_forge.config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings, get_settings
from campaign_forge.exceptions import ConfigurationError


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "groq_api_key": "k"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_defaults_are_sensible() -> None:
    settings = _settings()
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.model == DEFAULT_MODEL
    assert settings.max_workers >= 1
    assert settings.api_key == "k"


def test_openai_key_used_as_fallback() -> None:
    settings = Settings(_env_file=None, openai_api_key="oa")  # type: ignore[call-arg]
    assert settings.api_key == "oa"


def test_groq_key_takes_precedence() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, groq_api_key="groq", openai_api_key="oa"
    )
    assert settings.api_key == "groq"


def test_missing_api_key_raises_only_on_access() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    # Construction must not raise (no import-time / construction-time side effects).
    with pytest.raises(ConfigurationError, match="No API key"):
        _ = settings.api_key


def test_log_level_is_normalised() -> None:
    assert _settings(log_level="debug").log_level == "DEBUG"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(log_level="verbose")


@pytest.mark.parametrize(
    "field,value",
    [("temperature", 3.0), ("max_tokens", 0), ("max_workers", 0)],
)
def test_out_of_range_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
