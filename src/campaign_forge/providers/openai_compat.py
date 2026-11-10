"""The single provider implementation for every OpenAI-compatible endpoint.

It evolved from the v1 ``LLMClient`` and keeps that client's guarantees:

* **Explicit, logged, bounded retries on transient failures only.** Transient
  means rate limits with a short wait, timeouts, connection errors and 5xx.
  Permanent errors fail fast.
* **Server retry hints are honoured,** up to a maximum backoff. A hint longer
  than that maximum (e.g. "try again in 7m") is not slept through: the error is
  raised so the caller can pause the run instead of blocking a thread.
* **Daily or monthly quota exhaustion is never retried.**
* **A hard per-request timeout.**
* **Fail-loud handling of empty, truncated or content-filtered responses.**

New in 2.0:

* every vendor error is normalised into a :class:`~campaign_forge.exceptions.ProviderError`;
* token usage is captured from each response;
* error messages are redacted, because some vendors echo part of the key back.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from tenacity import (
    RetryCallState,
    Retrying,
    before_sleep_log,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity import retry_if_exception as tenacity_retry_if_exception

from ..domain.usage import TokenUsage
from ..exceptions import (
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
from ..logging_config import get_logger
from ..security.redaction import redact
from .base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    HealthStatus,
    ModelInfo,
)
from .profiles import ProviderProfile
from .retry import retry_after_seconds

DEFAULT_MAX_BACKOFF_SECONDS = 60.0


class OpenAICompatibleProvider(BaseProvider):
    """A resilient chat-completions client for one vendor profile."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.5,
        max_tokens: int = 800,
        timeout: float = 60.0,
        max_retries: int = 4,
        max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
        client: OpenAI | None = None,
    ) -> None:
        """Create a provider.

        Args:
            profile: The vendor profile (endpoint, quota patterns, notices).
            model: Default model id for requests that do not name one.
            api_key: Required unless ``client`` is injected.
            base_url: Overrides the profile's endpoint (e.g. a resolved Cloudflare URL).
            client: A pre-built OpenAI-compatible client. Tests inject a fake here.
        """
        if client is None and not api_key:
            raise ConfigurationError(
                f"No API key for {profile.display_name}. Set {profile.api_key_env or 'one'}."
            )
        self._profile = profile
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._max_backoff = max_backoff
        self._secrets = (api_key,) if api_key else ()
        self._log = get_logger(f"providers.{profile.name}")
        self._fallback_wait = wait_exponential_jitter(initial=1.0, max=max_backoff)
        # The SDK ships its own retry logic; disable it so this class is the
        # single source of truth for backoff and logging.
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url or profile.base_url,
            timeout=timeout,
            max_retries=0,
        )

    # ------------------------------------------------------------------ #
    # Provider protocol
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self._profile.name

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def profile(self) -> ProviderProfile:
        return self._profile

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run one chat completion with bounded retries.

        Raises:
            ProviderError: (an ``LLMError``) on failure, already normalised.
        """
        kwargs: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": (
                request.temperature if request.temperature is not None else self._temperature
            ),
            "max_tokens": request.max_tokens or self._max_tokens,
        }
        if request.json_mode and self._profile.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        extra = self._profile.params_for(kwargs["model"])
        if extra:
            kwargs["extra_body"] = extra

        attempts = 0
        lock = threading.Lock()

        def attempt() -> CompletionResponse:
            nonlocal attempts
            with lock:
                attempts += 1
            return self._create_once(kwargs)

        start = time.perf_counter()
        response = self._make_retrying()(attempt)
        return response.model_copy(
            update={"latency_s": time.perf_counter() - start, "attempts": attempts}
        )

    def health_check(self) -> HealthStatus:
        """List models as a cheap reachability and credentials check."""
        start = time.perf_counter()
        try:
            self._client.models.list()
        except OpenAIError as exc:
            error = self._normalise(exc)
            return HealthStatus(
                provider=self.name,
                ok=False,
                latency_s=time.perf_counter() - start,
                detail=f"{type(error).__name__}: {error}",
            )
        return HealthStatus(
            provider=self.name, ok=True, latency_s=time.perf_counter() - start, detail="reachable"
        )

    def list_models(self) -> list[str]:
        """Model ids the endpoint reports as available to this key."""
        try:
            page = self._client.models.list()
        except OpenAIError as exc:
            raise self._normalise(exc) from exc
        return sorted(str(item.id) for item in page)

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self._model,
            free_tier=self._profile.free_tier,
            json_mode=self._profile.json_mode,
            data_use_notice=self._profile.data_use_notice,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _make_retrying(self) -> Retrying:
        """A fresh controller per request, so concurrent calls never share retry state."""
        return Retrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._wait,
            retry=tenacity_retry_if_exception(self._should_retry),
            before_sleep=before_sleep_log(self._log, logging.WARNING),
            reraise=True,
        )

    def _should_retry(self, exc: BaseException) -> bool:
        if not isinstance(exc, ProviderError) or not exc.retryable:
            return False
        if isinstance(exc, RateLimitedError) and exc.retry_after is not None:
            # Do not block a worker thread for minutes: surface it so the run can pause.
            return exc.retry_after <= self._max_backoff
        return True

    def _wait(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        if isinstance(exc, RateLimitedError) and exc.retry_after is not None:
            # A small buffer so we do not wake fractionally too early.
            return min(exc.retry_after + 0.5, self._max_backoff)
        return float(self._fallback_wait(retry_state))

    def _create_once(self, kwargs: dict[str, Any]) -> CompletionResponse:
        """One API call (the unit that is retried), with response validation."""
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            raise self._normalise(exc) from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise EmptyResponseError("Model returned no choices.", provider=self.name)
        choice = choices[0]
        content = choice.message.content
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            raise ContentFilteredError(
                "The provider withheld the response (content filter).", provider=self.name
            )
        if not content or not content.strip():
            raise EmptyResponseError("Model returned an empty response.", provider=self.name)
        # A ``length`` finish reason means the model was cut off at the token
        # limit. The text looks fine but is truncated (and any JSON is likely
        # incomplete), so fail loudly rather than return a partial result.
        if finish_reason == "length":
            raise TruncatedResponseError(
                "Model response was truncated at the token limit. Increase CF_MAX_TOKENS "
                "(or this agent's max_tokens) and retry.",
                provider=self.name,
            )
        return CompletionResponse(
            text=content.strip(),
            provider=self.name,
            model=str(getattr(response, "model", None) or kwargs["model"]),
            finish_reason=finish_reason,
            usage=_extract_usage(response),
        )

    def _normalise(self, exc: OpenAIError) -> ProviderError:
        """Map an SDK exception onto the provider-independent error hierarchy."""
        message = redact(str(exc), self._secrets)
        name = self.name
        prefix = f"{self._profile.display_name}: "
        if isinstance(exc, RateLimitError) or (
            isinstance(exc, APIStatusError) and exc.status_code == 429
        ):
            hint = retry_after_seconds(exc)
            if self._profile.is_quota_message(message):
                return QuotaExhaustedError(
                    f"{prefix}quota exhausted. {message}", provider=name, resume_after=hint
                )
            return RateLimitedError(
                f"{prefix}rate limited. {message}", provider=name, retry_after=hint
            )
        if isinstance(exc, APITimeoutError):  # subclass of APIConnectionError: check first
            return ProviderTimeoutError(f"{prefix}request timed out.", provider=name)
        if isinstance(exc, APIConnectionError):
            return ProviderUnavailableError(f"{prefix}connection failed. {message}", provider=name)
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return ProviderAuthError(f"{prefix}not authorised. {message}", provider=name)
        if isinstance(exc, (BadRequestError, NotFoundError, UnprocessableEntityError)):
            advice = ""
            if "model_not_found" in message or "does not exist" in message:
                advice = (
                    " The model may have been retired: list available models with "
                    "'python scripts/provider_smoke.py --live --models' and set CF_MODEL or "
                    "CF_PROVIDER_MODELS."
                )
            return ProviderBadRequestError(
                f"{prefix}request rejected. {message}{advice}", provider=name
            )
        if isinstance(exc, InternalServerError) or (
            isinstance(exc, APIStatusError) and exc.status_code >= 500
        ):
            return ProviderUnavailableError(f"{prefix}server error. {message}", provider=name)
        return ProviderError(f"{prefix}request failed. {message}", provider=name)


def _extract_usage(response: object) -> TokenUsage:
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if isinstance(prompt, int) and isinstance(completion, int):
        return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)
    return TokenUsage.unreported()
