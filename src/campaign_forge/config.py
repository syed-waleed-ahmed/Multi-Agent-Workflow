"""Application configuration.

Settings are loaded from environment variables and an optional ``.env`` file.
Importing this module has **no side effects** - nothing is validated until a
:class:`Settings` instance is created, and a missing API key only raises when
:attr:`Settings.api_key` is actually accessed. This keeps the package safe to
import in tests and other tooling that never talks to a model provider.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.1-8b-instant"
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class Settings(BaseSettings):
    """Runtime configuration for Campaign Forge.

    Values are resolved (in priority order) from constructor arguments,
    environment variables, then a ``.env`` file. Provider-agnostic knobs use the
    ``CF_`` prefix (e.g. ``CF_MODEL``); the API keys keep their conventional
    unprefixed names so existing setups keep working.
    """

    model_config = SettingsConfigDict(
        env_prefix="CF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,  # allow Settings(groq_api_key=...) as well as the env alias
    )

    # --- Authentication -----------------------------------------------------
    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "CF_GROQ_API_KEY"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "CF_OPENAI_API_KEY"),
    )

    # --- Provider / model ---------------------------------------------------
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, gt=0, le=32_000)

    # --- Resilience ---------------------------------------------------------
    request_timeout: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=4, ge=0, le=10)

    # --- Batch / scale ------------------------------------------------------
    max_workers: int = Field(default=4, ge=1, le=64)

    # --- Output / logging ---------------------------------------------------
    output_dir: Path = Path("outputs")
    log_level: str = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            upper = value.upper()
            if upper not in _VALID_LOG_LEVELS:
                raise ValueError(
                    f"Invalid log level {value!r}; choose one of {sorted(_VALID_LOG_LEVELS)}."
                )
            return upper
        return value

    @property
    def api_key(self) -> str:
        """Return the resolved API key.

        Raises:
            ConfigurationError: if neither ``GROQ_API_KEY`` nor ``OPENAI_API_KEY``
                is set.
        """
        key = self.groq_api_key or self.openai_api_key
        if not key:
            raise ConfigurationError(
                "No API key found. Set GROQ_API_KEY (or OPENAI_API_KEY) in your "
                "environment or .env file. See .env.example for details."
            )
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
