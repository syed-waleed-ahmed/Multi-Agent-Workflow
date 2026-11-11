"""Application configuration.

Settings are loaded from environment variables and an optional ``.env`` file.
Importing this module has **no side effects**: nothing is validated until a
:class:`Settings` instance is created, and a missing API key only raises when a
key is actually needed. This keeps the package safe to import in tests and
tooling that never talks to a model provider.

Long-lived processes (a web UI or API server) should build a fresh
:class:`Settings` per session or request. The cached :func:`get_settings` exists
for one-shot CLI use and backwards compatibility; see :func:`reload_settings`.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
# Verified live on 2026-11-10; llama-3.1-8b-instant was retired by Groq (D11, ADR-028).
DEFAULT_MODEL = "qwen/qwen3.8-27b"
MAX_WORKERS = 64
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_PROVIDER_NAME_PATTERN = r"^[a-z][a-z0-9_-]*$"

# Settings fields that hold secrets, in the order they are searched for redaction.
SECRET_FIELDS: tuple[str, ...] = (
    "groq_api_key",
    "openai_api_key",
    "gemini_api_key",
    "mistral_api_key",
    "cloudflare_api_token",
    "tavily_api_key",
    "api_keys",
)


class AgentRoute(BaseModel):
    """Per-agent (or per-run) provider overrides. Unset fields fall through to defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str | None = Field(default=None, pattern=_PROVIDER_NAME_PATTERN)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=32_000)


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

    # --- Authentication (held as SecretStr so they never appear in reprs) ---
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "CF_GROQ_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "CF_OPENAI_API_KEY"),
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "CF_GEMINI_API_KEY"),
    )
    mistral_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MISTRAL_API_KEY", "CF_MISTRAL_API_KEY"),
    )
    cloudflare_api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDFLARE_API_TOKEN", "CF_CLOUDFLARE_API_TOKEN"),
    )
    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "CF_TAVILY_API_KEY"),
    )
    cloudflare_account_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDFLARE_ACCOUNT_ID", "CF_CLOUDFLARE_ACCOUNT_ID"),
    )

    # --- Provider / model ---------------------------------------------------
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, gt=0, le=32_000)
    provider: str | None = Field(default=None, pattern=_PROVIDER_NAME_PATTERN)
    provider_models: dict[str, str] = Field(default_factory=dict)
    agent_overrides: dict[str, AgentRoute] = Field(default_factory=dict)
    fallback_providers: str = ""
    free_only: bool = True
    #: Per-provider limit on requests in flight, e.g. {"groq": 4} on a paid tier.
    provider_concurrency: dict[str, int] = Field(default_factory=dict)

    # --- Research tools -----------------------------------------------------
    research_tools: str = "wikipedia"

    # --- Resilience ---------------------------------------------------------
    request_timeout: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=4, ge=0, le=10)

    # --- Batch / scale ------------------------------------------------------
    max_workers: int = Field(default=4, ge=1, le=MAX_WORKERS)

    # --- Output / logging ---------------------------------------------------
    output_dir: Path = Path("outputs")
    runs_dir: Path = Path("outputs/runs")
    log_level: str = "INFO"

    # --- Memory and persistence (ADR-031) -----------------------------------
    #: SQLite file holding brand profiles, campaign history and notes (and runs
    #: when ``run_store`` is ``sqlite``).
    memory_db: Path = Path("outputs/campaign_forge.db")
    run_store: Literal["json", "sqlite"] = "json"
    #: Remember completed CLI runs as searchable campaign history.
    memory_history: bool = True

    # --- Streamlit app (Session 6) ------------------------------------------
    #: ``demo``: simulated output, nothing stored on the server (the public default,
    #: ADR-008). ``local``: your configured provider, run store and memory.
    ui_mode: Literal["demo", "local"] = "demo"

    # --- API (Session 7, ADR-034) -------------------------------------------
    #: Comma-separated API keys. Each key is its own tenant. Without keys, every
    #: protected endpoint answers 401: the API is closed by default.
    api_keys: SecretStr | None = None
    #: Requests per minute per key, and run starts per minute per key.
    api_rate_limit: int = Field(default=120, ge=1, le=10_000)
    api_run_limit: int = Field(default=10, ge=1, le=1_000)
    #: Background runs executing at once in one API process.
    api_max_active_runs: int = Field(default=4, ge=1, le=64)

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
        """Return the v1 API key (Groq, else OpenAI).

        Raises:
            ConfigurationError: if neither ``GROQ_API_KEY`` nor ``OPENAI_API_KEY``
                is set.
        """
        key = self.secret("groq_api_key") or self.secret("openai_api_key")
        if not key:
            raise ConfigurationError(
                "No API key found. Set GROQ_API_KEY (or OPENAI_API_KEY) in your "
                "environment or .env file. See .env.example for details."
            )
        return key

    def secret(self, field_name: str) -> str | None:
        """Return the plain value of a secret field, or ``None`` when it is unset or empty."""
        if field_name not in SECRET_FIELDS:
            raise ValueError(f"{field_name!r} is not a secret field.")
        value: SecretStr | None = getattr(self, field_name)
        if value is None:
            return None
        plain = value.get_secret_value()
        return plain or None

    def secret_values(self) -> tuple[str, ...]:
        """Every configured secret, for log redaction (each API key separately too)."""
        values = [self.secret(name) for name in SECRET_FIELDS]
        return tuple(v for v in [*values, *self.api_key_values] if v)

    @property
    def api_key_values(self) -> tuple[str, ...]:
        """``CF_API_KEYS`` parsed (comma-separated, blanks dropped, de-duplicated)."""
        raw = self.secret("api_keys") or ""
        return tuple(dict.fromkeys(k.strip() for k in raw.split(",") if k.strip()))

    @property
    def research_tool_names(self) -> tuple[str, ...]:
        """``CF_RESEARCH_TOOLS`` parsed (comma-separated; empty disables research tools)."""
        names: list[str] = []
        for raw in self.research_tools.split(","):
            name = raw.strip().lower()
            if name and name not in names:
                names.append(name)
        return tuple(names)

    @property
    def fallback_provider_names(self) -> tuple[str, ...]:
        """``CF_FALLBACK_PROVIDERS`` parsed into an ordered, de-duplicated tuple."""
        names: list[str] = []
        for raw in self.fallback_providers.split(","):
            name = raw.strip().lower()
            if name and name not in names:
                names.append(name)
        return tuple(names)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance (CLI convenience)."""
    return Settings()


def reload_settings() -> Settings:
    """Discard the cached settings and re-read the environment."""
    get_settings.cache_clear()
    return get_settings()


def apply_overrides(settings: Settings, overrides: Mapping[str, Any]) -> Settings:
    """Return a copy of ``settings`` with ``overrides`` applied **and validated**.

    ``model_copy(update=...)`` skips validation, which let invalid CLI values
    (e.g. ``--workers -1``) through. This helper re-validates the merged values.

    Raises:
        ConfigurationError: if any override is invalid.
    """
    if not overrides:
        return settings
    data = settings.model_dump()
    data.update(overrides)
    try:
        validated = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid setting override:\n{exc}") from exc
    # Copy only the validated override values so ``model_fields_set`` keeps
    # recording which settings the user set explicitly.
    return settings.model_copy(update={name: getattr(validated, name) for name in overrides})
