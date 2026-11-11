"""Campaign Forge - a production-grade multi-agent marketing campaign generator.

Typical library usage::

    from campaign_forge import CampaignBrief, CampaignWorkflow

    brief = CampaignBrief(
        product_name="EcoSip Bottle",
        product_description="Insulated bottle that keeps drinks cold for 24h.",
        target_audience="eco-conscious young professionals",
        goal="drive summer sales",
        tone="fresh and energetic",
        channels=["instagram", "email"],
    )
    result = CampaignWorkflow().run(brief)
    print(result.final_brief)

The 2.0 foundation (agent registry, providers, budgets, artifacts) lives in the
:mod:`campaign_forge.core`, :mod:`campaign_forge.providers`,
:mod:`campaign_forge.domain` and :mod:`campaign_forge.security` subpackages.
The most useful names are re-exported here.
"""

from __future__ import annotations

from .config import AgentRoute, Settings, apply_overrides, get_settings, reload_settings
from .core import (
    AgentCategory,
    AgentOutput,
    AgentRegistry,
    AgentSpec,
    Budget,
    BudgetLimits,
    EventBus,
    RunContext,
)
from .domain import Artifact, Basis, Claim, Evidence, ProducedBy, Scope, TokenUsage
from .exceptions import (
    AgentError,
    BudgetExceededError,
    CampaignForgeError,
    ConfigurationError,
    LLMError,
    OutputParsingError,
    ProviderError,
    ProviderNotAllowedError,
    QuotaExhaustedError,
    RateLimitedError,
    RegistryError,
    RunCancelledError,
)
from .llm import LLMClient
from .loaders import load_briefs
from .models import CampaignBrief, CampaignResult, CopyContent
from .providers import (
    CompletionRequest,
    CompletionResponse,
    FakeProvider,
    ModelClass,
    OpenAICompatibleProvider,
    ProviderRouter,
)
from .workflow import BatchItemResult, CampaignWorkflow

__version__ = "2.0.0b1"

__all__ = [
    "AgentCategory",
    "AgentError",
    "AgentOutput",
    "AgentRegistry",
    "AgentRoute",
    "AgentSpec",
    "Artifact",
    "Basis",
    "BatchItemResult",
    "Budget",
    "BudgetExceededError",
    "BudgetLimits",
    "CampaignBrief",
    "CampaignForgeError",
    "CampaignResult",
    "CampaignWorkflow",
    "Claim",
    "CompletionRequest",
    "CompletionResponse",
    "ConfigurationError",
    "CopyContent",
    "EventBus",
    "Evidence",
    "FakeProvider",
    "LLMClient",
    "LLMError",
    "ModelClass",
    "OpenAICompatibleProvider",
    "OutputParsingError",
    "ProducedBy",
    "ProviderError",
    "ProviderNotAllowedError",
    "ProviderRouter",
    "QuotaExhaustedError",
    "RateLimitedError",
    "RegistryError",
    "RunCancelledError",
    "RunContext",
    "Scope",
    "Settings",
    "TokenUsage",
    "__version__",
    "apply_overrides",
    "get_settings",
    "load_briefs",
    "reload_settings",
]
