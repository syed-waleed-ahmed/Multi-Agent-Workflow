"""Provider-independent contracts: requests, responses and the ``Provider`` protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.usage import TokenUsage

ModelT = TypeVar("ModelT", bound=BaseModel)

Role = Literal["system", "user", "assistant"]


class ModelClass(str, Enum):
    """Coarse model capability classes that agents request instead of concrete model ids."""

    SMALL = "small"
    STANDARD = "standard"
    STRONG = "strong"


class ChatMessage(BaseModel):
    """One message in a chat completion request."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class CompletionRequest(BaseModel):
    """A provider-independent chat completion request.

    ``model``, ``temperature`` and ``max_tokens`` may be left unset; the provider
    (or router) fills them from configuration. ``metadata`` is never sent to the
    provider. It carries routing and logging context such as the agent id.
    """

    model_config = ConfigDict(frozen=True)

    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=32_000)
    json_mode: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_prompts(
        cls,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool = False,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CompletionRequest:
        """Build the common two-message (system + user) request."""
        return cls(
            messages=(
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ),
            json_mode=json_mode,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata or {},
        )


class CompletionResponse(BaseModel):
    """The normalised result of one successful completion."""

    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: TokenUsage = TokenUsage.unreported()
    latency_s: float = Field(default=0.0, ge=0.0)
    attempts: int = Field(default=1, ge=1)
    fallback_from: str | None = None


class HealthStatus(BaseModel):
    """Result of a provider health check. ``detail`` never contains secrets."""

    model_config = ConfigDict(frozen=True)

    provider: str
    ok: bool
    latency_s: float = 0.0
    detail: str = ""


class ModelInfo(BaseModel):
    """Static facts about the provider and default model, shown in the UI and docs."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    free_tier: bool
    json_mode: bool
    data_use_notice: str


@dataclass(frozen=True)
class StructuredResult(Generic[ModelT]):
    """A validated structured output plus every response it took to get there."""

    value: ModelT
    responses: tuple[CompletionResponse, ...]

    @property
    def repairs(self) -> int:
        """How many corrective re-asks were needed (0 when the first answer was valid)."""
        return len(self.responses) - 1

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for response in self.responses:
            total = total + response.usage
        return total


@runtime_checkable
class Provider(Protocol):
    """What every LLM provider implementation offers."""

    @property
    def name(self) -> str: ...

    @property
    def default_model(self) -> str: ...

    def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def complete_structured(
        self, request: CompletionRequest, schema: type[ModelT], *, max_repairs: int = 1
    ) -> StructuredResult[ModelT]: ...

    def health_check(self) -> HealthStatus: ...

    def model_info(self) -> ModelInfo: ...


class BaseProvider(ABC):
    """Shared behaviour: structured output is built once on top of :meth:`complete`."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def default_model(self) -> str: ...

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    @abstractmethod
    def health_check(self) -> HealthStatus: ...

    @abstractmethod
    def model_info(self) -> ModelInfo: ...

    def complete_structured(
        self, request: CompletionRequest, schema: type[ModelT], *, max_repairs: int = 1
    ) -> StructuredResult[ModelT]:
        """Request JSON, then parse and validate it against ``schema``, re-asking on failure."""
        from .structured import run_structured

        return run_structured(self.complete, request, schema, max_repairs=max_repairs)
