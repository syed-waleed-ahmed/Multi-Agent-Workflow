"""The v1 ``LLMClient``, kept as a thin compatibility facade over the 2.0 provider layer.

All transport behaviour now lives in
:class:`~campaign_forge.providers.openai_compat.OpenAICompatibleProvider`:
bounded retries on transient errors only, honouring ``Retry-After``, a hard
timeout, and fail-loud empty or truncated responses. This class keeps the v1
signature (``complete(system, user, *, json_mode) -> str``) so existing callers
and the v1 agents work unchanged.

Not covered by ``FREE_ONLY`` policy: the facade keeps v1 behaviour and calls
whatever ``CF_BASE_URL`` points at. The 2.0 router enforces the policy; see
ADR-020 in ``docs/DECISIONS.md``.
"""

from __future__ import annotations

import copy

from openai import OpenAI

from .config import AgentRoute, Settings
from .logging_config import get_logger
from .providers.base import CompletionRequest, CompletionResponse
from .providers.openai_compat import OpenAICompatibleProvider
from .providers.profiles import custom_profile, profile_for_base_url
from .providers.retry import retry_after_seconds as _retry_after_seconds

__all__ = ["LLMClient", "_retry_after_seconds"]


class LLMClient:
    """Wraps an OpenAI-compatible endpoint with retries, timeouts and logging."""

    def __init__(self, settings: Settings, *, client: OpenAI | None = None) -> None:
        """Create a client.

        Args:
            settings: Resolved application settings.
            client: An optional pre-built ``OpenAI`` client. Injecting one is how
                tests supply a fake; in production it is built from ``settings``.
        """
        self._settings = settings
        self._route: AgentRoute | None = None
        self._log = get_logger("llm")
        profile = profile_for_base_url(settings.base_url) or custom_profile(settings.base_url)
        self._provider = OpenAICompatibleProvider(
            profile,
            model=settings.model,
            api_key=None if client is not None else settings.api_key,
            base_url=settings.base_url,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            client=client,
        )

    @property
    def model(self) -> str:
        if self._route is not None and self._route.model:
            return self._route.model
        return self._settings.model

    @property
    def provider(self) -> OpenAICompatibleProvider:
        """The underlying 2.0 provider."""
        return self._provider

    def for_agent(self, agent_name: str) -> LLMClient:
        """Return a view of this client that applies ``CF_AGENT_OVERRIDES[agent_name]``.

        Model, temperature and max_tokens overrides apply here. A ``provider``
        override needs the 2.0 router and is ignored by this facade, with a warning.
        """
        route = self._settings.agent_overrides.get(agent_name)
        if route is None:
            return self
        if route.provider:
            self._log.warning(
                "Agent override for %r names provider %r; the v1 pipeline ignores provider "
                "overrides (use the 2.0 router).",
                agent_name,
                route.provider,
            )
        view = copy.copy(self)
        view._route = route
        return view

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool = False,
    ) -> str:
        """Run a single chat completion and return the message text.

        Raises:
            LLMError: if the request fails after all retries, or the model
                returns an empty or truncated response.
        """
        return self.complete_with_usage(system_prompt, user_prompt, json_mode=json_mode).text

    def complete_with_usage(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool = False,
    ) -> CompletionResponse:
        """Like :meth:`complete`, but return the full response including token usage."""
        route = self._route
        request = CompletionRequest.from_prompts(
            system_prompt,
            user_prompt,
            json_mode=json_mode,
            model=route.model if route else None,
            temperature=route.temperature if route else None,
            max_tokens=route.max_tokens if route else None,
        )
        return self._provider.complete(request)
