"""Exception hierarchy for Campaign Forge.

A single base exception (:class:`CampaignForgeError`) lets callers catch every
error this package raises, while the specific subclasses allow fine-grained
handling (e.g. treating configuration problems differently from transient API
failures).

Provider failures are normalised into :class:`ProviderError` subclasses so the
rest of the system can react to *what happened* (rate limited, quota exhausted,
bad credentials, ...) without knowing which vendor SDK raised it. Every
provider error is also an :class:`LLMError`, so v1 code that catches
``LLMError`` keeps working unchanged.
"""

from __future__ import annotations


class CampaignForgeError(Exception):
    """Base class for every error raised by Campaign Forge."""


class ConfigurationError(CampaignForgeError):
    """Raised when required configuration (e.g. an API key) is missing or invalid."""


class ProviderNotAllowedError(ConfigurationError):
    """Raised when a provider or model is refused by policy (e.g. ``FREE_ONLY`` mode)."""


class LLMError(CampaignForgeError):
    """Raised when the language-model provider fails after exhausting retries."""


class ProviderError(LLMError):
    """A normalised provider failure.

    Attributes:
        provider: Name of the provider profile that failed (``"unknown"`` if not known).
        retryable: Whether retrying the same request may succeed.
    """

    retryable: bool = False

    def __init__(self, message: str, *, provider: str = "unknown") -> None:
        self.provider = provider
        super().__init__(message)


class RateLimitedError(ProviderError):
    """A short-term rate limit (per minute / per second). Retrying later may succeed.

    Attributes:
        retry_after: Server-suggested delay in seconds, when one was supplied.
    """

    retryable = True

    def __init__(
        self, message: str, *, provider: str = "unknown", retry_after: float | None = None
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, provider=provider)


class QuotaExhaustedError(ProviderError):
    """A daily or monthly quota is used up. Retrying now is pointless; resume later.

    Attributes:
        resume_after: Server-suggested delay in seconds before capacity returns, if known.
    """

    def __init__(
        self, message: str, *, provider: str = "unknown", resume_after: float | None = None
    ) -> None:
        self.resume_after = resume_after
        super().__init__(message, provider=provider)


class ProviderAuthError(ProviderError):
    """Credentials were rejected or the account may not use the requested model."""


class ProviderBadRequestError(ProviderError):
    """The provider rejected the request itself (unknown model, invalid parameters, ...)."""


class ProviderTimeoutError(ProviderError):
    """The request timed out."""

    retryable = True


class ProviderUnavailableError(ProviderError):
    """Connection failure or a server-side (5xx) error."""

    retryable = True


class EmptyResponseError(ProviderError):
    """The provider returned no choices or empty content."""


class TruncatedResponseError(ProviderError):
    """The response was cut off at the token limit (``finish_reason == "length"``)."""


class ContentFilteredError(ProviderError):
    """The provider withheld the response because of a content filter."""


class AgentError(CampaignForgeError):
    """Raised when an individual agent cannot produce a usable result.

    Attributes:
        agent: Human-readable name of the agent that failed.
    """

    def __init__(self, agent: str, message: str) -> None:
        self.agent = agent
        super().__init__(f"[{agent}] {message}")


class OutputParsingError(AgentError):
    """Raised when an agent's raw model output cannot be parsed into the expected shape."""


class StepTimeoutError(AgentError):
    """Raised when a workflow step exceeds its time limit."""


class RegistryError(CampaignForgeError):
    """Raised for agent-registry problems (duplicate ids, unknown or disabled agents)."""


class BudgetExceededError(CampaignForgeError):
    """Raised when a run would exceed one of its hard limits.

    Attributes:
        limit: Which limit was hit (e.g. ``"llm_calls"``, ``"tokens"``, ``"wall_time"``).
    """

    def __init__(self, limit: str, message: str) -> None:
        self.limit = limit
        super().__init__(message)


class RunCancelledError(CampaignForgeError):
    """Raised when work is attempted after a run was cancelled."""


class WorkflowValidationError(ConfigurationError):
    """Raised when a workflow definition is invalid (structure, agents, inputs or limits).

    Attributes:
        problems: Every problem found, so all of them can be fixed in one pass.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid workflow:\n- " + "\n- ".join(problems))


class RunStateError(CampaignForgeError):
    """Raised for an illegal run-state transition or an operation the run's state forbids."""


class RunNotFoundError(CampaignForgeError):
    """Raised when a run id is unknown in the given scope."""
