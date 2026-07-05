"""A thin, resilient wrapper around an OpenAI-compatible chat API.

The wrapper adds the things a production workload needs and the raw SDK call
does not provide out of the box in a controllable way:

* explicit, logged retries with exponential backoff on *transient* failures
  (rate limits, timeouts, connection resets, 5xx) - permanent errors such as
  auth/validation failures fail fast;
* a hard per-request timeout;
* empty-response detection;
* a single place to inject a fake client for testing.
"""

from __future__ import annotations

import logging
import re

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam
from tenacity import (
    RetryCallState,
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Settings
from .exceptions import LLMError
from .logging_config import get_logger

# Errors that are worth retrying - everything else (bad request, auth) fails fast.
_TRANSIENT_ERRORS: tuple[type[OpenAIError], ...] = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

_MAX_BACKOFF_SECONDS = 60.0
_RETRY_AFTER_MESSAGE_RE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
# Fallback exponential backoff (with jitter) when the server gives no hint.
_FALLBACK_WAIT = wait_exponential_jitter(initial=1.0, max=_MAX_BACKOFF_SECONDS)


def _retry_after_seconds(exc: BaseException | None) -> float | None:
    """Extract a server-suggested retry delay from a rate-limit error, if any.

    Prefers the standard ``Retry-After`` HTTP header and falls back to parsing
    the provider's message (e.g. Groq's "Please try again in 5.66s").
    """
    if exc is None:
        return None

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        header_value = headers.get("retry-after")
        if header_value:
            try:
                return float(header_value)
            except ValueError:
                pass

    message = str(getattr(exc, "message", "") or exc)
    match = _RETRY_AFTER_MESSAGE_RE.search(message)
    if match:
        return float(match.group(1))
    return None


def _rate_limit_aware_wait(retry_state: RetryCallState) -> float:
    """Honour a server ``Retry-After`` hint, else fall back to jittered backoff."""
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    hinted = _retry_after_seconds(exc)
    if hinted is not None:
        # Add a small buffer so we don't wake up fractionally too early.
        return min(hinted + 0.5, _MAX_BACKOFF_SECONDS)
    return _FALLBACK_WAIT(retry_state)


class LLMClient:
    """Wraps an OpenAI-compatible client with retries, timeouts and logging."""

    def __init__(self, settings: Settings, *, client: OpenAI | None = None) -> None:
        """Create a client.

        Args:
            settings: Resolved application settings.
            client: An optional pre-built ``OpenAI`` client. Injecting one is how
                tests supply a fake; in production it is built from ``settings``.
        """
        self._settings = settings
        self._log = get_logger("llm")
        # The SDK ships its own retry logic; disable it so tenacity is the single
        # source of truth for backoff and logging.
        self._client = client or OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout,
            max_retries=0,
        )

    def _make_retrying(self) -> Retrying:
        """Build a fresh retry controller.

        A new controller is created per request because ``Retrying`` keeps
        mutable per-run state; a fresh instance keeps ``complete`` safe to call
        concurrently from the batch thread pool.
        """
        return Retrying(
            stop=stop_after_attempt(self._settings.max_retries + 1),
            wait=_rate_limit_aware_wait,
            retry=retry_if_exception_type(_TRANSIENT_ERRORS),
            before_sleep=before_sleep_log(self._log, logging.WARNING),
            reraise=True,
        )

    @property
    def model(self) -> str:
        return self._settings.model

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool = False,
    ) -> str:
        """Run a single chat completion and return the message text.

        Args:
            system_prompt: The system role instruction.
            user_prompt: The user role content.
            json_mode: When ``True``, ask the provider to return a JSON object.

        Returns:
            The stripped assistant message content.

        Raises:
            LLMError: if the request fails after all retries, or the model
                returns an empty response.
        """
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            content: str = self._make_retrying()(self._create, messages, json_mode)
        except OpenAIError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        return content

    def _create(self, messages: list[ChatCompletionMessageParam], json_mode: bool) -> str:
        """Perform one API call (the unit that tenacity retries)."""
        if json_mode:
            response = self._client.chat.completions.create(
                model=self._settings.model,
                messages=messages,
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
                response_format={"type": "json_object"},
            )
        else:
            response = self._client.chat.completions.create(
                model=self._settings.model,
                messages=messages,
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
            )

        if not response.choices:
            raise LLMError("Model returned no choices.")
        choice = response.choices[0]
        content = choice.message.content
        if not content or not content.strip():
            raise LLMError("Model returned an empty response.")
        # A ``length`` finish reason means the model was cut off at the token
        # limit. The text looks fine but is truncated mid-thought (and any JSON
        # is likely incomplete), so fail loudly rather than save a partial brief.
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMError(
                "Model response was truncated at the token limit. Increase CF_MAX_TOKENS and retry."
            )
        return content.strip()
