"""LLM providers: one OpenAI-compatible implementation, vendor profiles, a fake, a router."""

from __future__ import annotations

from .base import (
    BaseProvider,
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    HealthStatus,
    ModelClass,
    ModelInfo,
    Provider,
    StructuredResult,
)
from .fake import FakeProvider
from .openai_compat import OpenAICompatibleProvider
from .profiles import (
    BUILTIN_PROFILES,
    ProviderProfile,
    custom_profile,
    get_profile,
    profile_for_base_url,
)
from .retry import parse_duration_hint, retry_after_seconds
from .router import ProviderRouter, ResolvedRoute
from .structured import run_structured

__all__ = [
    "BUILTIN_PROFILES",
    "BaseProvider",
    "ChatMessage",
    "CompletionRequest",
    "CompletionResponse",
    "FakeProvider",
    "HealthStatus",
    "ModelClass",
    "ModelInfo",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderProfile",
    "ProviderRouter",
    "ResolvedRoute",
    "StructuredResult",
    "custom_profile",
    "get_profile",
    "parse_duration_hint",
    "profile_for_base_url",
    "retry_after_seconds",
    "run_structured",
]
