"""Vendor profiles: data-only descriptions of OpenAI-compatible providers.

Free-tier facts change often, so they live here as data instead of being spread
through the code. The values were verified on 2026-11-10; see
``docs/FREE_TIER_POLICY.md`` for sources and the re-verification schedule.

Default model ids are set only where they are known to work (Groq's v1 default).
For the other vendors, configure a model with ``CF_PROVIDER_MODELS`` or a
per-agent override. That is safer than shipping guessed model ids that may
already be retired.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cached_property
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..exceptions import ConfigurationError
from .base import ModelClass

# Markers that distinguish a daily/monthly quota from a short-term rate limit.
GENERIC_QUOTA_PATTERNS: tuple[str, ...] = (
    r"per[ _-]?day",
    r"perday",
    r"daily",
    r"per[ _-]?month",
    r"monthly",
    r"\bTPD\b",
    r"\bRPD\b",
)


class ProviderProfile(BaseModel):
    """Everything Campaign Forge needs to know about one OpenAI-compatible vendor."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str
    base_url: str
    api_key_field: str | None = None
    api_key_env: str = ""
    account_id_field: str | None = None
    account_id_env: str = ""
    default_model: str | None = None
    model_aliases: Mapping[str, str] = Field(default_factory=dict)
    free_tier: bool
    json_mode: bool = True
    quota_patterns: tuple[str, ...] = GENERIC_QUOTA_PATTERNS
    #: Extra request parameters by model-id prefix (sent as ``extra_body``).
    model_params: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)
    #: Hard per-request completion caps by model-id prefix. A request asking for more
    #: is clamped, because the provider would reject it outright.
    max_output_tokens: Mapping[str, int] = Field(default_factory=dict)
    #: Requests in flight at once. On an output-token-capped free tier, parallel requests
    #: only displace each other (and burn retries), so they are serialised.
    max_concurrency: int | None = Field(default=None, ge=1, le=64)
    data_use_notice: str
    docs_url: str = ""

    @cached_property
    def _quota_re(self) -> re.Pattern[str] | None:
        if not self.quota_patterns:
            return None
        return re.compile("|".join(f"(?:{p})" for p in self.quota_patterns), re.IGNORECASE)

    def is_quota_message(self, message: str) -> bool:
        """True when a rate-limit message describes a daily or monthly quota."""
        pattern = self._quota_re
        return bool(pattern and pattern.search(message))

    def params_for(self, model: str) -> dict[str, Any]:
        """Extra request parameters for ``model`` (longest matching prefix wins)."""
        matches = [prefix for prefix in self.model_params if model.startswith(prefix)]
        return dict(self.model_params[max(matches, key=len)]) if matches else {}

    def output_cap_for(self, model: str) -> int | None:
        """The per-request completion cap for ``model`` (longest matching prefix), if any."""
        matches = [prefix for prefix in self.max_output_tokens if model.startswith(prefix)]
        return self.max_output_tokens[max(matches, key=len)] if matches else None

    def model_for(self, model_class: ModelClass | None) -> str | None:
        """The model for a capability class, else the profile default."""
        if model_class is not None and model_class.value in self.model_aliases:
            return self.model_aliases[model_class.value]
        return self.default_model

    def resolve_base_url(self, account_id: str | None = None) -> str:
        """The base URL with any ``{account_id}`` placeholder filled in."""
        if "{account_id}" not in self.base_url:
            return self.base_url
        if not account_id:
            raise ConfigurationError(
                f"{self.display_name} needs an account id. Set {self.account_id_env or 'it'}."
            )
        return self.base_url.replace("{account_id}", account_id)


GROQ = ProviderProfile(
    name="groq",
    display_name="Groq",
    base_url="https://api.groq.com/openai/v1",
    api_key_field="groq_api_key",
    api_key_env="GROQ_API_KEY",
    # Verified live on 2026-11-10 (scripts/provider_smoke.py --live --models). The v1 default
    # llama-3.1-8b-instant no longer exists on Groq. qwen3.8-27b answers without hidden
    # reasoning tokens, which matters under the free tier's 8,000 tokens-per-minute limit.
    default_model="qwen/qwen3.8-27b",
    model_aliases={
        ModelClass.SMALL.value: "qwen/qwen3.8-27b",
        ModelClass.STANDARD.value: "qwen/qwen3.8-27b",
        ModelClass.STRONG.value: "openai/gpt-oss-120b",
    },
    # gpt-oss models spend completion tokens on reasoning; keep that small.
    model_params={"openai/gpt-oss": {"reasoning_effort": "low"}},
    # Verified live 2026-11-10: on the free tier qwen3.8-27b allows 1,000 output tokens per
    # minute, and a request whose max_tokens exceeds that is rejected ("Request too large").
    max_output_tokens={"qwen/qwen3.8": 1000},
    # Verified live 2026-11-10: two parallel requests under the 1,000 output-token/minute cap
    # exhausted their retries. One request at a time completes. Override: CF_PROVIDER_CONCURRENCY.
    max_concurrency=1,
    free_tier=True,
    data_use_notice=(
        "Groq's data-use terms for the free plan were not verified on 2026-11-10. "
        "Avoid confidential input."
    ),
    docs_url="https://console.groq.com/docs/rate-limits",
)

GEMINI = ProviderProfile(
    name="gemini",
    display_name="Google Gemini",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key_field="gemini_api_key",
    api_key_env="GEMINI_API_KEY",
    free_tier=True,
    data_use_notice=(
        "On the free tier Google uses submitted content to improve its products, and "
        "human reviewers may read it. Google offers only paid services to users in "
        "the EEA, Switzerland and the UK."
    ),
    docs_url="https://ai.google.dev/gemini-api/docs/pricing",
)

MISTRAL = ProviderProfile(
    name="mistral",
    display_name="Mistral AI",
    base_url="https://api.mistral.ai/v1",
    api_key_field="mistral_api_key",
    api_key_env="MISTRAL_API_KEY",
    free_tier=True,
    data_use_notice=(
        "Mistral's documentation conflicts on whether free-plan API data is used for "
        "training. Opt out under Admin > Privacy and avoid confidential input."
    ),
    docs_url="https://help.mistral.ai/en/articles/698531",
)

CLOUDFLARE = ProviderProfile(
    name="cloudflare",
    display_name="Cloudflare Workers AI",
    base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
    api_key_field="cloudflare_api_token",
    api_key_env="CLOUDFLARE_API_TOKEN",
    account_id_field="cloudflare_account_id",
    account_id_env="CLOUDFLARE_ACCOUNT_ID",
    free_tier=True,
    quota_patterns=(*GENERIC_QUOTA_PATTERNS, r"account limited", r"\b3036\b"),
    data_use_notice="Cloudflare's data-use terms were not verified on 2026-11-10.",
    docs_url="https://developers.cloudflare.com/workers-ai/platform/pricing/",
)

OPENAI = ProviderProfile(
    name="openai",
    display_name="OpenAI",
    base_url="https://api.openai.com/v1",
    api_key_field="openai_api_key",
    api_key_env="OPENAI_API_KEY",
    free_tier=False,
    data_use_notice="Paid provider; refused while FREE_ONLY mode is on.",
    docs_url="https://platform.openai.com/docs/pricing",
)

FAKE = ProviderProfile(
    name="fake",
    display_name="Simulated provider",
    base_url="memory://fake",
    default_model="fake-model",
    free_tier=True,
    data_use_notice="Simulated responses; no data leaves this process.",
)

BUILTIN_PROFILES: Mapping[str, ProviderProfile] = {
    profile.name: profile for profile in (GROQ, GEMINI, MISTRAL, CLOUDFLARE, OPENAI, FAKE)
}


def get_profile(name: str) -> ProviderProfile:
    """Look up a built-in profile by name."""
    try:
        return BUILTIN_PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(BUILTIN_PROFILES))
        raise ConfigurationError(f"Unknown provider {name!r}. Known providers: {known}.") from None


def _normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def profile_for_base_url(base_url: str) -> ProviderProfile | None:
    """Return the built-in profile whose endpoint matches ``base_url``, if any."""
    target = _normalise_url(base_url)
    for profile in BUILTIN_PROFILES.values():
        if "{account_id}" in profile.base_url:
            prefix, suffix = profile.base_url.split("{account_id}", 1)
            if target.startswith(_normalise_url(prefix)) and target.endswith(suffix.rstrip("/")):
                return profile
        elif _normalise_url(profile.base_url) == target:
            return profile
    return None


def custom_profile(base_url: str) -> ProviderProfile:
    """A profile for an unrecognised endpoint. Its cost is unknown, so it is not free-eligible."""
    return ProviderProfile(
        name="custom",
        display_name=f"Custom endpoint ({urlsplit(base_url).netloc or base_url})",
        base_url=base_url,
        api_key_field=None,
        free_tier=False,
        data_use_notice="Unknown endpoint: its cost and data-use terms are unknown.",
    )
