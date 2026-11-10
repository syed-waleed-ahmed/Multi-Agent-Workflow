"""A deterministic, offline provider for tests and the public demo mode.

Responses come from a responder, which can be one of:

* a fixed string;
* a mapping keyed by the ``agent`` metadata of the request;
* a callable that receives the request.

Scripted failures are raised before any responses, so tests can drive retries,
quota pauses and fallbacks without a network. Token usage is estimated from word
counts and marked as reported, which keeps budget arithmetic deterministic.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping

from ..domain.usage import TokenUsage
from ..exceptions import ConfigurationError
from .base import BaseProvider, CompletionRequest, CompletionResponse, HealthStatus, ModelInfo
from .profiles import FAKE

Responder = Callable[[CompletionRequest], str]


class FakeProvider(BaseProvider):
    """An in-memory provider with scripted responses and failures."""

    def __init__(
        self,
        responder: Responder | Mapping[str, str] | str | None = None,
        *,
        name: str = FAKE.name,
        model: str = "fake-model",
        failures: Iterable[BaseException] = (),
        finish_reason: str = "stop",
    ) -> None:
        self._responder = responder
        self._name = name
        self._model = model
        self._failures: deque[BaseException] = deque(failures)
        self._finish_reason = finish_reason
        self._lock = threading.Lock()
        self.requests: list[CompletionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        with self._lock:
            self.requests.append(request)
            failure = self._failures.popleft() if self._failures else None
        if failure is not None:
            raise failure
        text = self._respond(request)
        prompt_words = sum(len(m.content.split()) for m in request.messages)
        return CompletionResponse(
            text=text,
            provider=self._name,
            model=request.model or self._model,
            finish_reason=self._finish_reason,
            usage=TokenUsage(prompt_tokens=prompt_words, completion_tokens=len(text.split())),
        )

    def health_check(self) -> HealthStatus:
        return HealthStatus(provider=self._name, ok=True, detail="simulated")

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self._name,
            model=self._model,
            free_tier=True,
            json_mode=True,
            data_use_notice=FAKE.data_use_notice,
        )

    def _respond(self, request: CompletionRequest) -> str:
        responder = self._responder
        if responder is None:
            return f"Simulated response {len(self.requests)} from {self._name}."
        if isinstance(responder, str):
            return responder
        if isinstance(responder, Mapping):
            agent = request.metadata.get("agent", "")
            if agent not in responder:
                raise ConfigurationError(
                    f"FakeProvider has no scripted response for agent {agent!r}."
                )
            return responder[agent]
        return responder(request)
