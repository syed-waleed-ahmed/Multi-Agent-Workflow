"""Provider routing: which provider and model serves each agent, under which policy.

Each setting is resolved from the first source that defines it:

1. an explicit per-run override;
2. the agent's entry in ``CF_AGENT_OVERRIDES``;
3. the agent spec's provider preference;
4. the global provider (``CF_PROVIDER``, else inferred from ``CF_BASE_URL``).

The model is resolved the same way. After the overrides, it comes from
``CF_MODEL`` (when routing to the global provider), then ``CF_PROVIDER_MODELS``,
then the profile's alias for the agent's model class, then the profile default.

Policy:

* **FREE_ONLY.** While ``CF_FREE_ONLY`` is true, only profiles marked free-tier
  eligible can run. Unknown endpoints are refused.
* **No silent substitution.** A fallback provider is tried only when it is listed
  in ``CF_FALLBACK_PROVIDERS``, only after a quota or rate-limit failure, and the
  response records ``fallback_from`` so the substitution is visible downstream.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import AgentRoute, Settings
from ..exceptions import (
    ConfigurationError,
    ProviderNotAllowedError,
    QuotaExhaustedError,
    RateLimitedError,
)
from ..logging_config import get_logger
from .base import CompletionRequest, CompletionResponse, ModelClass, Provider
from .openai_compat import OpenAICompatibleProvider
from .profiles import ProviderProfile, custom_profile, get_profile, profile_for_base_url

ProviderFactory = Callable[[ProviderProfile, "ResolvedRoute"], Provider]

#: Agent ids under this prefix also match the v1 override keys without it.
LEGACY_PREFIX = "legacy."


@dataclass(frozen=True)
class ResolvedRoute:
    """The concrete provider, model and sampling parameters for one call."""

    provider: str
    model: str
    temperature: float
    max_tokens: int


class ProviderRouter:
    """Resolves routes, enforces policy and performs calls with explicit fallback."""

    def __init__(
        self,
        settings: Settings,
        *,
        providers: Mapping[str, Provider] | None = None,
        factory: ProviderFactory | None = None,
    ) -> None:
        """Create a router.

        Args:
            settings: Resolved settings (keys, defaults, overrides, policy).
            providers: Pre-built providers by profile name. Tests and demo mode use this.
            factory: Builds a provider for a profile on first use (defaults to
                :class:`OpenAICompatibleProvider`).
        """
        self._settings = settings
        self._providers: dict[str, Provider] = dict(providers or {})
        self._factory = factory or self._default_factory
        self._lock = threading.Lock()
        self._log = get_logger("providers.router")
        inferred = profile_for_base_url(settings.base_url)
        self._global_profile = (
            get_profile(settings.provider)
            if settings.provider
            else inferred or custom_profile(settings.base_url)
        )

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    @property
    def global_provider(self) -> str:
        return self._global_profile.name

    def profile(self, name: str) -> ProviderProfile:
        if name == self._global_profile.name:
            return self._global_profile
        return get_profile(name)

    def add_provider(self, name: str, provider: Provider) -> None:
        """Use ``provider`` for profile ``name`` (e.g. an injected test or demo provider).

        Policy still applies: routes to ``name`` pass the FREE_ONLY check in :meth:`resolve`.
        """
        self.profile(name)  # must be a known profile
        with self._lock:
            self._providers[name] = provider

    def _configured(self, agent_id: str | None) -> AgentRoute | None:
        """The ``CF_AGENT_OVERRIDES`` entry for an agent.

        The v1 keys (``manager``, ``research``, ...) also apply to the engine's wrapped
        ``legacy.*`` agents, so existing v1 configuration keeps working.
        """
        if not agent_id:
            return None
        overrides = self._settings.agent_overrides
        configured = overrides.get(agent_id)
        if configured is None and agent_id.startswith(LEGACY_PREFIX):
            configured = overrides.get(agent_id[len(LEGACY_PREFIX) :])
        return configured

    def resolve(
        self,
        agent_id: str | None = None,
        *,
        model_class: ModelClass | None = None,
        provider_preference: str | None = None,
        override: AgentRoute | None = None,
    ) -> ResolvedRoute:
        """Resolve the route for an agent and enforce the FREE_ONLY policy.

        Raises:
            ProviderNotAllowedError: if policy refuses the provider.
            ConfigurationError: if no model can be resolved.
        """
        settings = self._settings
        configured = self._configured(agent_id)
        layers = [layer for layer in (override, configured) if layer is not None]

        provider_name = next(
            (layer.provider for layer in layers if layer.provider),
            provider_preference or self._global_profile.name,
        )
        profile = self.profile(provider_name)
        self.ensure_allowed(profile)

        # A layer's model only applies to its own provider: switching provider in a
        # higher layer must not send one vendor's model id to another vendor.
        model = next(
            (
                layer.model
                for layer in layers
                if layer.model and layer.provider in (None, profile.name)
            ),
            None,
        )
        if model is None:
            model = self._default_model(profile, model_class)
        temperature = next(
            (layer.temperature for layer in layers if layer.temperature is not None),
            settings.temperature,
        )
        max_tokens = next(
            (layer.max_tokens for layer in layers if layer.max_tokens is not None),
            settings.max_tokens,
        )
        return ResolvedRoute(
            provider=profile.name, model=model, temperature=temperature, max_tokens=max_tokens
        )

    def configured_max_tokens(
        self, agent_id: str | None, override: AgentRoute | None = None
    ) -> int | None:
        """The max_tokens explicitly configured for an agent, if any (not the global default)."""
        configured = self._configured(agent_id)
        for layer in (override, configured):
            if layer is not None and layer.max_tokens is not None:
                return layer.max_tokens
        return None

    def ensure_allowed(self, profile: ProviderProfile) -> None:
        """Raise if ``FREE_ONLY`` policy refuses ``profile``."""
        if self._settings.free_only and not profile.free_tier:
            raise ProviderNotAllowedError(
                f"{profile.display_name} is not free-tier eligible and FREE_ONLY mode is on. "
                "Choose a free provider, or set CF_FREE_ONLY=false to allow it deliberately."
            )

    def _default_model(self, profile: ProviderProfile, model_class: ModelClass | None) -> str:
        settings = self._settings
        is_global = profile.name == self._global_profile.name
        if is_global and "model" in settings.model_fields_set:
            return settings.model  # an explicit CF_MODEL wins for the global provider (v1)
        model = settings.provider_models.get(profile.name) or profile.model_for(model_class)
        if model:
            return model
        if is_global:
            return settings.model  # v1 default for an unrecognised global endpoint
        raise ConfigurationError(
            f"No model configured for provider {profile.name!r}. Set CF_PROVIDER_MODELS "
            f'(e.g. {{"{profile.name}": "<model-id>"}}) or a per-agent override.'
        )
        return model

    # ------------------------------------------------------------------ #
    # Calls
    # ------------------------------------------------------------------ #
    def provider(self, name: str, route: ResolvedRoute | None = None) -> Provider:
        """Return (building on first use) the provider instance for ``name``."""
        with self._lock:
            existing = self._providers.get(name)
            if existing is not None:
                return existing
            profile = self.profile(name)
            self.ensure_allowed(profile)
            built = self._factory(profile, route or self.resolve(provider_preference=name))
            self._providers[name] = built
            return built

    def complete(
        self,
        agent_id: str | None,
        request: CompletionRequest,
        *,
        model_class: ModelClass | None = None,
        provider_preference: str | None = None,
        override: AgentRoute | None = None,
    ) -> CompletionResponse:
        """Route and perform one completion, falling back only where configured."""
        route = self.resolve(
            agent_id,
            model_class=model_class,
            provider_preference=provider_preference,
            override=override,
        )
        try:
            return self._call(route, request, agent_id)
        except (QuotaExhaustedError, RateLimitedError) as primary_error:
            for name in self._settings.fallback_provider_names:
                if name == route.provider:
                    continue
                try:
                    fallback_route = self.resolve(
                        agent_id, model_class=model_class, override=AgentRoute(provider=name)
                    )
                except ConfigurationError as exc:
                    self._log.warning("Skipping fallback provider %r: %s", name, exc)
                    continue
                self._log.warning(
                    "Provider %r unavailable (%s); falling back to %r as configured.",
                    route.provider,
                    type(primary_error).__name__,
                    name,
                )
                try:
                    response = self._call(fallback_route, request, agent_id)
                except (QuotaExhaustedError, RateLimitedError):
                    continue
                return response.model_copy(update={"fallback_from": route.provider})
            raise

    def _call(
        self, route: ResolvedRoute, request: CompletionRequest, agent_id: str | None
    ) -> CompletionResponse:
        metadata = dict(request.metadata)
        if agent_id:
            metadata.setdefault("agent", agent_id)
        routed = request.model_copy(
            update={
                "model": route.model,
                "temperature": (
                    request.temperature if request.temperature is not None else route.temperature
                ),
                "max_tokens": request.max_tokens or route.max_tokens,
                "metadata": metadata,
            }
        )
        return self.provider(route.provider, route).complete(routed)

    def _default_factory(self, profile: ProviderProfile, route: ResolvedRoute) -> Provider:
        settings = self._settings
        if profile.name == "fake":
            from .fake import FakeProvider

            return FakeProvider(model=route.model)
        api_key = settings.secret(profile.api_key_field) if profile.api_key_field else None
        if profile.name == "custom":
            api_key = settings.api_key  # v1 behaviour for unrecognised endpoints
        if not api_key:
            raise ConfigurationError(
                f"No API key for {profile.display_name}. Set {profile.api_key_env}."
            )
        if profile.name == self._global_profile.name and profile.account_id_field is None:
            base_url = settings.base_url
        else:
            account_id = (
                getattr(settings, profile.account_id_field) if profile.account_id_field else None
            )
            base_url = profile.resolve_base_url(account_id)
        return OpenAICompatibleProvider(
            profile,
            model=route.model,
            api_key=api_key,
            base_url=base_url,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )
