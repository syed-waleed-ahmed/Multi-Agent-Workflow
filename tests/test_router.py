"""Tests for provider routing: resolution precedence, FREE_ONLY policy, explicit fallback."""

from __future__ import annotations

from typing import Any

import pytest

from campaign_forge.config import DEFAULT_MODEL, AgentRoute, Settings
from campaign_forge.exceptions import (
    ConfigurationError,
    ProviderAuthError,
    ProviderNotAllowedError,
    QuotaExhaustedError,
    RateLimitedError,
)
from campaign_forge.providers import (
    CompletionRequest,
    FakeProvider,
    ModelClass,
    OpenAICompatibleProvider,
    ProviderRouter,
)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"_env_file": None, "groq_api_key": "gsk_" + "a" * 20}
    base.update(overrides)
    return Settings(**base)


def _request() -> CompletionRequest:
    return CompletionRequest.from_prompts("sys", "user")


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_global_provider_inferred_from_base_url() -> None:
    assert ProviderRouter(_settings()).global_provider == "groq"
    custom = ProviderRouter(_settings(base_url="https://llm.example/v1", free_only=False))
    assert custom.global_provider == "custom"
    assert ProviderRouter(_settings(provider="gemini")).global_provider == "gemini"


def test_default_route_uses_v1_settings() -> None:
    route = ProviderRouter(_settings()).resolve("copywriter")
    assert (route.provider, route.model, route.temperature, route.max_tokens) == (
        "groq",
        DEFAULT_MODEL,
        0.5,
        800,
    )


def test_explicit_cf_model_wins_over_model_class_alias() -> None:
    router = ProviderRouter(_settings(model="my-model"))
    assert router.resolve("a", model_class=ModelClass.SMALL).model == "my-model"


def test_model_class_alias_used_when_model_not_explicit() -> None:
    router = ProviderRouter(_settings())
    assert router.resolve("a", model_class=ModelClass.SMALL).model == "qwen/qwen3.8-27b"
    assert router.resolve("a", model_class=ModelClass.STRONG).model == "openai/gpt-oss-120b"


def test_precedence_override_then_agent_config_then_preference() -> None:
    settings = _settings(
        agent_overrides={
            "writer": AgentRoute(provider="mistral", model="cfg-model", max_tokens=1500)
        },
        provider_models={"gemini": "gem-model", "mistral": "mis-model"},
    )
    router = ProviderRouter(settings)
    configured = router.resolve("writer")
    assert (configured.provider, configured.model, configured.max_tokens) == (
        "mistral",
        "cfg-model",
        1500,
    )
    overridden = router.resolve("writer", override=AgentRoute(provider="gemini", temperature=0.9))
    assert (overridden.provider, overridden.model, overridden.temperature) == (
        "gemini",
        "gem-model",  # the mistral-specific model must not leak to gemini
        0.9,
    )
    model_only = router.resolve("writer", override=AgentRoute(model="pinned"))
    assert (model_only.provider, model_only.model) == ("mistral", "pinned")
    preferred = router.resolve("other", provider_preference="gemini")
    assert (preferred.provider, preferred.model) == ("gemini", "gem-model")


def test_missing_model_for_non_global_provider_is_a_clear_error() -> None:
    router = ProviderRouter(_settings())
    with pytest.raises(ConfigurationError, match="CF_PROVIDER_MODELS"):
        router.resolve("a", provider_preference="gemini")


def test_custom_global_endpoint_uses_cf_model() -> None:
    router = ProviderRouter(_settings(base_url="https://llm.example/v1", free_only=False))
    assert router.resolve("a").model == DEFAULT_MODEL


def test_configured_max_tokens() -> None:
    router = ProviderRouter(_settings(agent_overrides={"m": AgentRoute(max_tokens=1200)}))
    assert router.configured_max_tokens("m") == 1200
    assert router.configured_max_tokens("m", AgentRoute(max_tokens=99)) == 99
    assert router.configured_max_tokens("other") is None
    assert router.configured_max_tokens(None) is None


# --------------------------------------------------------------------------- #
# FREE_ONLY policy
# --------------------------------------------------------------------------- #
def test_free_only_refuses_paid_and_unknown_providers() -> None:
    router = ProviderRouter(_settings(provider_models={"openai": "gpt-x"}))
    with pytest.raises(ProviderNotAllowedError, match="FREE_ONLY"):
        router.resolve("a", provider_preference="openai")
    custom = ProviderRouter(_settings(base_url="https://llm.example/v1"))
    with pytest.raises(ProviderNotAllowedError):
        custom.resolve("a")


def test_free_only_can_be_disabled_deliberately() -> None:
    router = ProviderRouter(_settings(free_only=False, provider_models={"openai": "gpt-x"}))
    assert router.resolve("a", provider_preference="openai").provider == "openai"


# --------------------------------------------------------------------------- #
# Calls and fallback
# --------------------------------------------------------------------------- #
def test_complete_routes_and_tags_metadata() -> None:
    fake = FakeProvider("done")
    router = ProviderRouter(_settings(), providers={"groq": fake})
    response = router.complete("copywriter", _request())
    assert response.text == "done"
    sent = fake.requests[0]
    assert sent.model == DEFAULT_MODEL
    assert sent.metadata["agent"] == "copywriter"
    assert sent.max_tokens == 800 and sent.temperature == 0.5


def test_no_fallback_without_explicit_configuration() -> None:
    primary = FakeProvider(failures=[QuotaExhaustedError("daily cap", provider="groq")])
    router = ProviderRouter(_settings(), providers={"groq": primary})
    with pytest.raises(QuotaExhaustedError):
        router.complete("a", _request())


def test_quota_exhaustion_falls_back_visibly_when_configured() -> None:
    primary = FakeProvider(failures=[QuotaExhaustedError("daily cap", provider="groq")])
    backup = FakeProvider("from backup", name="mistral")
    settings = _settings(fallback_providers="groq, mistral", provider_models={"mistral": "mis"})
    router = ProviderRouter(settings, providers={"groq": primary, "mistral": backup})
    response = router.complete("a", _request())
    assert response.text == "from backup"
    assert response.fallback_from == "groq"
    assert backup.requests[0].model == "mis"


def test_unresolvable_or_failing_fallbacks_reraise_original() -> None:
    primary = FakeProvider(failures=[RateLimitedError("slow", retry_after=600)])
    failing_backup = FakeProvider(name="mistral", failures=[QuotaExhaustedError("also out")])
    settings = _settings(fallback_providers="gemini,mistral", provider_models={"mistral": "m"})
    router = ProviderRouter(settings, providers={"groq": primary, "mistral": failing_backup})
    with pytest.raises(RateLimitedError):  # gemini has no model -> skipped; mistral fails
        router.complete("a", _request())


def test_auth_errors_do_not_trigger_fallback() -> None:
    primary = FakeProvider(failures=[ProviderAuthError("bad key")])
    backup = FakeProvider("unused", name="mistral")
    settings = _settings(fallback_providers="mistral", provider_models={"mistral": "m"})
    router = ProviderRouter(settings, providers={"groq": primary, "mistral": backup})
    with pytest.raises(ProviderAuthError):
        router.complete("a", _request())
    assert backup.call_count == 0


# --------------------------------------------------------------------------- #
# Provider construction
# --------------------------------------------------------------------------- #
def test_default_factory_builds_openai_compatible_provider_once() -> None:
    router = ProviderRouter(_settings())
    provider = router.provider("groq")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert router.provider("groq") is provider


def test_default_factory_requires_a_key() -> None:
    router = ProviderRouter(_settings(provider_models={"gemini": "g"}))
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        router.provider("gemini")


def test_default_factory_resolves_cloudflare_account_url() -> None:
    settings = _settings(
        cloudflare_api_token="t" * 20,
        cloudflare_account_id="acc-9",
        provider_models={"cloudflare": "@cf/model"},
    )
    provider = ProviderRouter(settings).provider("cloudflare")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert "/accounts/acc-9/" in str(provider._client.base_url)  # noqa: SLF001


def test_fake_profile_builds_fake_provider() -> None:
    router = ProviderRouter(_settings(provider="fake"))
    assert isinstance(router.provider("fake"), FakeProvider)
    assert router.complete("a", _request()).provider == "fake"


def test_custom_endpoint_uses_v1_key_when_allowed() -> None:
    router = ProviderRouter(_settings(base_url="https://llm.example/v1", free_only=False))
    provider = router.provider("custom")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert str(provider._client.base_url).startswith("https://llm.example/v1")  # noqa: SLF001
