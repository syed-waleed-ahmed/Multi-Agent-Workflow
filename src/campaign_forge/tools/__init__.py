"""Research tools: search adapters, the safe fetcher, user sources, and the toolbox."""

from __future__ import annotations

from ..config import Settings
from ..exceptions import ConfigurationError
from .base import EvidenceStore, SearchTool, ToolBox, ToolSpec
from .fetch import SafeFetcher
from .search import TavilySearch, WikipediaSearch
from .sources import bundle_evidence, load_source_file, load_source_url, load_sources

KNOWN_TOOLS = ("wikipedia", "tavily")


def build_toolbox(settings: Settings) -> ToolBox:
    """The search tools enabled by ``CF_RESEARCH_TOOLS`` (default: Wikipedia only).

    Raises:
        ConfigurationError: for an unknown tool name, or Tavily without a key.
    """
    tools: list[SearchTool] = []
    for name in settings.research_tool_names:
        if name == "wikipedia":
            tools.append(WikipediaSearch())
        elif name == "tavily":
            key = settings.secret("tavily_api_key")
            if not key:
                raise ConfigurationError("CF_RESEARCH_TOOLS includes tavily; set TAVILY_API_KEY.")
            tools.append(TavilySearch(key))
        else:
            known = ", ".join(KNOWN_TOOLS)
            raise ConfigurationError(f"Unknown research tool {name!r}. Known: {known}.")
    return ToolBox(tools)


__all__ = [
    "KNOWN_TOOLS",
    "EvidenceStore",
    "SafeFetcher",
    "SearchTool",
    "TavilySearch",
    "ToolBox",
    "ToolSpec",
    "WikipediaSearch",
    "build_toolbox",
    "bundle_evidence",
    "load_source_file",
    "load_source_url",
    "load_sources",
]
