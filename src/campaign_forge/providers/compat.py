"""Adapter that lets a v1-style chat client act as a 2.0 :class:`Provider`.

The v1 API accepts any object with ``complete(system, user, *, json_mode) -> str``
(``CampaignWorkflow(llm=...)``). That includes the test suite's ``FakeLLM`` and a
real :class:`~campaign_forge.llm.LLMClient`. Wrapping it as a provider lets those
objects drive the 2.0 engine unchanged.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.usage import TokenUsage
from ..exceptions import ProviderBadRequestError
from .base import BaseProvider, CompletionRequest, CompletionResponse, HealthStatus, ModelInfo


@runtime_checkable
class ChatClient(Protocol):
    """The v1 chat-client shape: a system prompt and a user prompt in, text out."""

    @property
    def model(self) -> str: ...

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str: ...


class LegacyClientProvider(BaseProvider):
    """Expose a :class:`ChatClient` through the provider protocol."""

    def __init__(self, client: ChatClient, *, name: str) -> None:
        self._client = client
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return str(self._client.model)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        roles = [m.role for m in request.messages]
        if roles != ["system", "user"]:
            raise ProviderBadRequestError(
                "A v1 chat client only supports one system and one user message.",
                provider=self._name,
            )
        system, user = (m.content for m in request.messages)
        with_usage = getattr(self._client, "complete_with_usage", None)
        if callable(with_usage):
            response = with_usage(system, user, json_mode=request.json_mode)
            if isinstance(response, CompletionResponse):
                return response.model_copy(update={"provider": self._name})
        text = self._client.complete(system, user, json_mode=request.json_mode)
        return CompletionResponse(
            text=text,
            provider=self._name,
            model=self.default_model,
            usage=TokenUsage.unreported(),
        )

    def health_check(self) -> HealthStatus:
        return HealthStatus(provider=self._name, ok=True, detail="injected v1 client")

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self._name,
            model=self.default_model,
            free_tier=True,
            json_mode=True,
            data_use_notice="Injected client; its provider's terms apply.",
        )
