"""Tests for provider profiles, retry hints, the OpenAI-compatible provider and the fake."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from campaign_forge.exceptions import (
    ConfigurationError,
    ContentFilteredError,
    EmptyResponseError,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitedError,
    TruncatedResponseError,
)
from campaign_forge.providers import (
    BUILTIN_PROFILES,
    CompletionRequest,
    FakeProvider,
    ModelClass,
    OpenAICompatibleProvider,
    Provider,
    custom_profile,
    get_profile,
    parse_duration_hint,
    profile_for_base_url,
    retry_after_seconds,
)

from .sdk_http import http as httpx

GROQ = BUILTIN_PROFILES["groq"]
_REQUEST = httpx.Request("POST", "https://api.example.test/v1/chat/completions")


# --------------------------------------------------------------------------- #
# Fakes and helpers
# --------------------------------------------------------------------------- #
def _status_error(
    cls: type[openai.APIStatusError],
    status: int,
    message: str,
    headers: dict[str, str] | None = None,
) -> openai.APIStatusError:
    response = httpx.Response(status, request=_REQUEST, headers=headers or {})
    return cls(message, response=response, body=None)


def _completion(
    content: str | None = "hello",
    *,
    finish_reason: str = "stop",
    usage: tuple[int, int] | None = (12, 3),
    model: str = "served-model",
    choices: bool = True,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)] if choices else [],
        usage=(
            SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
        ),
        model=model,
    )


class _Client:
    """A fake OpenAI client: completions come from ``responder``; models from ``models``."""

    def __init__(
        self,
        responder: Callable[[dict[str, Any]], Any],
        *,
        models: list[str] | Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []

        def create(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return responder(kwargs)

        def list_models() -> list[SimpleNamespace]:
            if isinstance(models, Exception):
                raise models
            return [SimpleNamespace(id=m) for m in (models or [])]

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        self.models = SimpleNamespace(list=list_models)


def _provider(
    responder: Callable[[dict[str, Any]], Any],
    *,
    profile: Any = GROQ,
    max_retries: int = 2,
    models: list[str] | Exception | None = None,
    api_key: str | None = None,
) -> tuple[OpenAICompatibleProvider, _Client]:
    client = _Client(responder, models=models)
    provider = OpenAICompatibleProvider(
        profile,
        model="default-model",
        api_key=api_key,
        max_retries=max_retries,
        client=client,  # type: ignore[arg-type]
    )
    return provider, client


def _request(**kwargs: Any) -> CompletionRequest:
    return CompletionRequest.from_prompts("sys", "user", **kwargs)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_: None)


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #
def test_profile_lookup_and_unknown_provider() -> None:
    assert get_profile("gemini").display_name == "Google Gemini"
    with pytest.raises(ConfigurationError, match="Unknown provider"):
        get_profile("nope")


def test_profile_detection_from_base_url() -> None:
    assert profile_for_base_url("https://api.groq.com/openai/v1/") is GROQ
    assert profile_for_base_url("HTTPS://API.GROQ.COM/openai/v1") is GROQ
    cloudflare = "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"
    assert profile_for_base_url(cloudflare) is BUILTIN_PROFILES["cloudflare"]
    assert profile_for_base_url("https://llm.internal.example/v1") is None


def test_custom_profile_is_not_free_eligible() -> None:
    profile = custom_profile("https://llm.internal.example/v1")
    assert profile.name == "custom"
    assert profile.free_tier is False
    assert "llm.internal.example" in profile.display_name


def test_model_for_uses_aliases_then_default() -> None:
    assert GROQ.model_for(ModelClass.SMALL) == "qwen/qwen3.8-27b"
    assert GROQ.model_for(ModelClass.STRONG) == "openai/gpt-oss-120b"
    assert GROQ.model_for(None) == GROQ.default_model
    assert BUILTIN_PROFILES["gemini"].model_for(ModelClass.SMALL) is None


def test_resolve_base_url_fills_account_id() -> None:
    cloudflare = BUILTIN_PROFILES["cloudflare"]
    assert "/accounts/acc-1/" in cloudflare.resolve_base_url("acc-1")
    with pytest.raises(ConfigurationError, match="CLOUDFLARE_ACCOUNT_ID"):
        cloudflare.resolve_base_url(None)
    assert GROQ.resolve_base_url("ignored") == GROQ.base_url


@pytest.mark.parametrize(
    "message,is_quota",
    [
        ("Rate limit reached on tokens per day (TPD): Limit 500000", True),
        ("Quota exceeded for metric GenerateRequestsPerDayPerProjectPerModel-FreeTier", True),
        ("Monthly token cap reached", True),
        ("Rate limit reached on tokens per minute (TPM). Please try again in 5s", False),
    ],
)
def test_quota_classification(message: str, is_quota: bool) -> None:
    assert GROQ.is_quota_message(message) is is_quota


def test_cloudflare_quota_code_is_recognised() -> None:
    assert BUILTIN_PROFILES["cloudflare"].is_quota_message("3036: Account limited")
    assert not custom_profile("https://x.test").is_quota_message("anything")


# --------------------------------------------------------------------------- #
# Retry hints
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "message,expected",
    [
        ("Please try again in 5.66s.", 5.66),
        ("Please try again in 7m12.5s.", 432.5),
        ("retry in 1h2m", 3720.0),
        ("Please try again in 250ms", 0.25),
        ("no hint here", None),
    ],
)
def test_parse_duration_hint(message: str, expected: float | None) -> None:
    assert parse_duration_hint(message) == expected


def test_retry_after_prefers_millisecond_header() -> None:
    exc = _status_error(
        openai.RateLimitError, 429, "slow down", {"retry-after-ms": "1500", "retry-after": "9"}
    )
    assert retry_after_seconds(exc) == 1.5


def test_retry_after_ignores_http_date_header() -> None:
    exc = _status_error(
        openai.RateLimitError, 429, "slow down", {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )
    assert retry_after_seconds(exc) is None


def test_retry_after_bad_ms_header_falls_back() -> None:
    exc = _status_error(
        openai.RateLimitError, 429, "slow", {"retry-after-ms": "soon", "retry-after": "4"}
    )
    assert retry_after_seconds(exc) == 4.0


# --------------------------------------------------------------------------- #
# OpenAICompatibleProvider
# --------------------------------------------------------------------------- #
def test_complete_returns_normalised_response_with_usage() -> None:
    provider, client = _provider(lambda _: _completion("  hi  "))
    response = provider.complete(_request(model="m1", temperature=0.1, max_tokens=50))
    assert response.text == "hi"
    assert response.provider == "groq"
    assert response.model == "served-model"
    assert response.usage.prompt_tokens == 12 and response.usage.complete
    assert response.attempts == 1
    assert client.calls[0]["model"] == "m1"
    assert client.calls[0]["temperature"] == 0.1
    assert client.calls[0]["max_tokens"] == 50
    assert "response_format" not in client.calls[0]


def test_defaults_fill_unset_request_fields() -> None:
    provider, client = _provider(lambda _: _completion(usage=None))
    response = provider.complete(_request())
    assert client.calls[0]["model"] == "default-model"
    assert client.calls[0]["max_tokens"] == 800
    assert response.usage.complete is False


def test_json_mode_respects_profile_support() -> None:
    provider, client = _provider(lambda _: _completion("{}"))
    provider.complete(_request(json_mode=True))
    assert client.calls[0]["response_format"] == {"type": "json_object"}

    no_json = GROQ.model_copy(update={"json_mode": False})
    provider, client = _provider(lambda _: _completion("{}"), profile=no_json)
    provider.complete(_request(json_mode=True))
    assert "response_format" not in client.calls[0]


@pytest.mark.parametrize(
    "completion,error",
    [
        (_completion(choices=False), EmptyResponseError),
        (_completion("   "), EmptyResponseError),
        (_completion("cut", finish_reason="length"), TruncatedResponseError),
        (_completion(None, finish_reason="content_filter"), ContentFilteredError),
    ],
)
def test_bad_responses_fail_loudly(completion: Any, error: type[Exception]) -> None:
    provider, client = _provider(lambda _: completion)
    with pytest.raises(error):
        provider.complete(_request())
    assert len(client.calls) == 1  # never retried


def _raising(exc: BaseException) -> Callable[[dict[str, Any]], Any]:
    def responder(_: dict[str, Any]) -> Any:
        raise exc

    return responder


@pytest.mark.parametrize(
    "exc,error,retried",
    [
        (openai.APITimeoutError(request=_REQUEST), ProviderTimeoutError, True),
        (openai.APIConnectionError(request=_REQUEST), ProviderUnavailableError, True),
        (_status_error(openai.InternalServerError, 500, "boom"), ProviderUnavailableError, True),
        (_status_error(openai.APIStatusError, 503, "unavailable"), ProviderUnavailableError, True),
        (_status_error(openai.AuthenticationError, 401, "bad key"), ProviderAuthError, False),
        (_status_error(openai.PermissionDeniedError, 403, "paid model"), ProviderAuthError, False),
        (_status_error(openai.BadRequestError, 400, "bad"), ProviderBadRequestError, False),
        (_status_error(openai.NotFoundError, 404, "no model"), ProviderBadRequestError, False),
        (_status_error(openai.APIStatusError, 409, "conflict"), ProviderError, False),
        (openai.OpenAIError("weird"), ProviderError, False),
    ],
)
def test_errors_are_normalised_and_retried_only_when_transient(
    exc: BaseException, error: type[Exception], retried: bool
) -> None:
    provider, client = _provider(_raising(exc), max_retries=2)
    with pytest.raises(error) as excinfo:
        provider.complete(_request())
    assert excinfo.value.provider == "groq"  # type: ignore[attr-defined]
    assert len(client.calls) == (3 if retried else 1)


def test_short_rate_limit_is_retried_then_succeeds() -> None:
    attempts = {"n": 0}

    def responder(_: dict[str, Any]) -> Any:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _status_error(openai.RateLimitError, 429, "Please try again in 2s.")
        return _completion("ok")

    provider, _ = _provider(responder)
    response = provider.complete(_request())
    assert response.text == "ok"
    assert response.attempts == 2


def test_long_rate_limit_hint_is_not_slept_through() -> None:
    exc = _status_error(openai.RateLimitError, 429, "Rate limit (TPM). Please try again in 7m12s.")
    provider, client = _provider(_raising(exc), max_retries=4)
    with pytest.raises(RateLimitedError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.retry_after == 432.0
    assert len(client.calls) == 1


def test_daily_quota_is_never_retried() -> None:
    exc = _status_error(
        openai.RateLimitError, 429, "Limit on tokens per day (TPD). Please try again in 3m."
    )
    provider, client = _provider(_raising(exc), max_retries=4)
    with pytest.raises(QuotaExhaustedError) as excinfo:
        provider.complete(_request())
    assert excinfo.value.resume_after == 180.0
    assert len(client.calls) == 1


def test_plain_429_status_error_is_treated_as_rate_limit() -> None:
    exc = _status_error(openai.APIStatusError, 429, "too many requests")
    provider, client = _provider(_raising(exc), max_retries=1)
    with pytest.raises(RateLimitedError):
        provider.complete(_request())
    assert len(client.calls) == 2


def test_error_messages_are_redacted() -> None:
    exc = _status_error(openai.AuthenticationError, 401, "Invalid key secret-key-123456 provided")
    provider, _ = _provider(_raising(exc), api_key="secret-key-123456")
    with pytest.raises(ProviderAuthError) as excinfo:
        provider.complete(_request())
    assert "secret-key-123456" not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)


def test_missing_api_key_without_client_raises() -> None:
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
        OpenAICompatibleProvider(GROQ, model="m")


def test_real_client_is_built_when_key_given() -> None:
    provider = OpenAICompatibleProvider(GROQ, model="m", api_key="k" * 20)
    assert provider.name == "groq"
    assert provider.default_model == "m"
    assert provider.profile is GROQ


def test_health_check_and_model_listing() -> None:
    provider, _ = _provider(lambda _: _completion(), models=["b-model", "a-model"])
    health = provider.health_check()
    assert health.ok and health.provider == "groq"
    assert provider.list_models() == ["a-model", "b-model"]
    info = provider.model_info()
    assert info.free_tier and info.model == "default-model"


def test_health_check_reports_failure_without_raising() -> None:
    failing = _status_error(openai.AuthenticationError, 401, "bad key")
    provider, _ = _provider(lambda _: _completion(), models=failing)
    health = provider.health_check()
    assert health.ok is False
    assert "ProviderAuthError" in health.detail
    with pytest.raises(ProviderAuthError):
        provider.list_models()


def test_provider_satisfies_protocol() -> None:
    provider, _ = _provider(lambda _: _completion())
    assert isinstance(provider, Provider)
    assert isinstance(FakeProvider(), Provider)


# --------------------------------------------------------------------------- #
# FakeProvider
# --------------------------------------------------------------------------- #
def test_fake_provider_responders() -> None:
    assert FakeProvider("fixed").complete(_request()).text == "fixed"
    by_agent = FakeProvider({"copywriter": "copy!"})
    request = _request(metadata={"agent": "copywriter"})
    assert by_agent.complete(request).text == "copy!"
    with pytest.raises(ConfigurationError, match="no scripted response"):
        by_agent.complete(_request(metadata={"agent": "other"}))
    echo = FakeProvider(lambda r: r.messages[-1].content.upper())
    assert echo.complete(_request()).text == "USER"
    default = FakeProvider()
    assert default.complete(_request()).text.startswith("Simulated response 1")


def test_fake_provider_scripted_failures_then_success() -> None:
    fake = FakeProvider("ok", failures=[RateLimitedError("slow", retry_after=1.0)])
    with pytest.raises(RateLimitedError):
        fake.complete(_request())
    assert fake.complete(_request()).text == "ok"
    assert fake.call_count == 2


def test_fake_provider_usage_and_metadata() -> None:
    fake = FakeProvider("three word answer", model="m-x")
    response = fake.complete(_request(model="override"))
    assert response.usage.completion_tokens == 3
    assert response.usage.prompt_tokens == 2  # "sys" + "user"
    assert response.model == "override"
    assert fake.health_check().ok
    assert fake.model_info().free_tier
    assert fake.default_model == "m-x"


def test_model_params_are_sent_for_matching_models() -> None:
    provider, client = _provider(lambda _: _completion("ok"))
    provider.complete(_request(model="openai/gpt-oss-120b"))
    provider.complete(_request(model="qwen/qwen3.8-27b"))
    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}
    assert "extra_body" not in client.calls[1]
    assert GROQ.params_for("openai/gpt-oss-20b") == {"reasoning_effort": "low"}
    assert GROQ.params_for("other") == {}


def test_retired_model_errors_explain_what_to_do() -> None:
    exc = _status_error(
        openai.NotFoundError, 404, "The model `old` does not exist (model_not_found)"
    )
    provider, _ = _provider(_raising(exc))
    with pytest.raises(ProviderBadRequestError, match="may have been retired"):
        provider.complete(_request())


def test_output_cap_clamps_requests_the_provider_would_reject() -> None:
    provider, client = _provider(lambda _: _completion("ok"))
    provider.complete(_request(model="qwen/qwen3.8-27b", max_tokens=1500))
    provider.complete(_request(model="openai/gpt-oss-120b", max_tokens=1500))
    assert client.calls[0]["max_tokens"] == 1000
    assert client.calls[1]["max_tokens"] == 1500
    assert GROQ.output_cap_for("qwen/qwen3.8-27b") == 1000 and GROQ.output_cap_for("x") is None


def test_request_too_large_is_a_bad_request_not_a_rate_limit() -> None:
    exc = _status_error(
        openai.RateLimitError,
        429,
        "Request too large for model on output tokens per minute (OTPM): Limit 1000, "
        "Requested 1500. The request's expected output tokens exceed the enforced limit",
    )
    provider, client = _provider(_raising(exc), max_retries=3)
    with pytest.raises(ProviderBadRequestError, match="Lower this agent's max_tokens"):
        provider.complete(_request())
    assert len(client.calls) == 1
