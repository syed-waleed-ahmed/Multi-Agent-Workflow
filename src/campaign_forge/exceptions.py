"""Exception hierarchy for Campaign Forge.

A single base exception (:class:`CampaignForgeError`) lets callers catch every
error this package raises, while the specific subclasses allow fine-grained
handling (e.g. treating configuration problems differently from transient API
failures).
"""

from __future__ import annotations


class CampaignForgeError(Exception):
    """Base class for every error raised by Campaign Forge."""


class ConfigurationError(CampaignForgeError):
    """Raised when required configuration (e.g. an API key) is missing or invalid."""


class LLMError(CampaignForgeError):
    """Raised when the language-model provider fails after exhausting retries."""


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
