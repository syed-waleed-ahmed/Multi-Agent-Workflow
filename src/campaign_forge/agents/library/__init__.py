"""The 2.0 agent library, wave 1 (docs/AGENT_CATALOG.md).

Eleven agents, each with a distinct output contract:

* research: ``audience_research``, ``market_research``
* strategy: ``positioning``, ``marketing_strategist``
* content: ``copywriter``, ``ad_copy``, ``social_media``, ``email_marketing``
* creative: ``art_director``
* quality: ``critic``
* synthesis: ``campaign_reporter``
"""

from __future__ import annotations

from ...core.registry import AgentRegistry
from .content import AdCopyAgent, CopywriterAgent, EmailMarketingAgent, SocialMediaAgent
from .creative import ArtDirectionAgent
from .quality import CriticAgent, JudgeVerdict, rubric_score
from .reporting import CampaignReporterAgent
from .research import AudienceResearchAgent, MarketResearchAgent
from .strategy import MarketingStrategistAgent, PositioningAgent

LIBRARY_AGENTS: tuple[type, ...] = (
    AudienceResearchAgent,
    MarketResearchAgent,
    PositioningAgent,
    MarketingStrategistAgent,
    CopywriterAgent,
    AdCopyAgent,
    SocialMediaAgent,
    EmailMarketingAgent,
    ArtDirectionAgent,
    CriticAgent,
    CampaignReporterAgent,
)


def register_library_agents(registry: AgentRegistry) -> None:
    """Register the wave-1 agent library."""
    for agent_cls in LIBRARY_AGENTS:
        registry.register(agent_cls)


__all__ = [
    "LIBRARY_AGENTS",
    "AdCopyAgent",
    "ArtDirectionAgent",
    "AudienceResearchAgent",
    "CampaignReporterAgent",
    "CopywriterAgent",
    "CriticAgent",
    "EmailMarketingAgent",
    "JudgeVerdict",
    "MarketResearchAgent",
    "MarketingStrategistAgent",
    "PositioningAgent",
    "SocialMediaAgent",
    "register_library_agents",
    "rubric_score",
]
