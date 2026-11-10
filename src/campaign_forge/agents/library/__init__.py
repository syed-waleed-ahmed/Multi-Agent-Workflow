"""The 2.0 agent library, wave 1 (docs/AGENT_CATALOG.md).

Thirteen agents, each with a distinct output contract (11 from wave 1, plus the
Session 4 evidence agents ``competitor_research`` and ``fact_checker``):

* research: ``audience_research``, ``market_research``, ``competitor_research``
* strategy: ``positioning``, ``marketing_strategist``
* content: ``copywriter``, ``ad_copy``, ``social_media``, ``email_marketing``
* creative: ``art_director``
* quality: ``critic``, ``fact_checker``
* synthesis: ``campaign_reporter``
"""

from __future__ import annotations

from ...core.registry import AgentRegistry
from .content import AdCopyAgent, CopywriterAgent, EmailMarketingAgent, SocialMediaAgent
from .creative import ArtDirectionAgent
from .quality import CriticAgent, FactCheckerAgent, JudgeVerdict, rubric_score
from .reporting import CampaignReporterAgent
from .research import AudienceResearchAgent, CompetitorResearchAgent, MarketResearchAgent
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
    CompetitorResearchAgent,
    FactCheckerAgent,
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
    "CompetitorResearchAgent",
    "CopywriterAgent",
    "CriticAgent",
    "EmailMarketingAgent",
    "FactCheckerAgent",
    "JudgeVerdict",
    "MarketResearchAgent",
    "MarketingStrategistAgent",
    "PositioningAgent",
    "SocialMediaAgent",
    "register_library_agents",
    "rubric_score",
]
