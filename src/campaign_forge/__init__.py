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
"""

from __future__ import annotations

from .config import Settings, get_settings
from .exceptions import (
    AgentError,
    CampaignForgeError,
    ConfigurationError,
    LLMError,
    OutputParsingError,
)
from .llm import LLMClient
from .loaders import load_briefs
from .models import CampaignBrief, CampaignResult, CopyContent
from .workflow import BatchItemResult, CampaignWorkflow

__version__ = "1.1.0"

__all__ = [
    "AgentError",
    "BatchItemResult",
    "CampaignBrief",
    "CampaignForgeError",
    "CampaignResult",
    "CampaignWorkflow",
    "ConfigurationError",
    "CopyContent",
    "LLMClient",
    "LLMError",
    "OutputParsingError",
    "Settings",
    "__version__",
    "get_settings",
    "load_briefs",
]
