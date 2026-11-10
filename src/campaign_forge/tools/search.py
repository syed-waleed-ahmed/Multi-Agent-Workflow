"""Search tools: Wikipedia (keyless) and Tavily (optional, the user's own free key).

Both return :class:`Evidence` built from the provider's own snippets. Neither
fetches arbitrary pages.

* **Wikipedia:** follows the Wikimedia API etiquette. It sends a descriptive
  User-Agent with a project contact URL, makes one request per query, and runs
  no concurrent bursts.
* **Tavily:** used only when ``TAVILY_API_KEY`` is set (1,000 free credits a
  month; see docs/FREE_TIER_POLICY.md). Error messages are redacted.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.provenance import Evidence
from ..exceptions import ToolError
from ..security.redaction import redact
from .base import ToolSpec
from .fetch import USER_AGENT

WIKIPEDIA_SPEC = ToolSpec(
    id="wikipedia",
    name="Wikipedia",
    description="Encyclopedia summaries via the Wikimedia API (no key needed).",
)
TAVILY_SPEC = ToolSpec(
    id="tavily",
    name="Tavily web search",
    description="Web search snippets via Tavily (needs TAVILY_API_KEY; free tier available).",
    requires_key=True,
)
MAX_RESULTS = 5


class WikipediaSearch:
    """Wikipedia search, returning each result's introduction as evidence."""

    def __init__(
        self, *, client: httpx.Client | None = None, language: str = "en", timeout: float = 10.0
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._endpoint = f"https://{language}.wikipedia.org/w/api.php"

    @property
    def spec(self) -> ToolSpec:
        return WIKIPEDIA_SPEC

    def search(self, query: str, *, limit: int = 3) -> list[Evidence]:
        params: dict[str, Any] = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": max(1, min(limit, MAX_RESULTS)),
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "inprop": "url",
        }
        try:
            response = self._client.get(
                self._endpoint, params=params, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(f"Wikipedia search failed: {type(exc).__name__}.") from exc
        pages = sorted(pages, key=lambda p: p.get("index", 0))
        return [
            Evidence.create(
                source=str(page.get("fullurl") or self._endpoint),
                title=str(page.get("title", "")),
                excerpt=str(page.get("extract", ""))[:8000],
            )
            for page in pages
            if str(page.get("extract", "")).strip()
        ]


class TavilySearch:
    """Tavily web search, returning each result's content snippet as evidence."""

    def __init__(
        self, api_key: str, *, client: httpx.Client | None = None, timeout: float = 15.0
    ) -> None:
        if not api_key:
            raise ToolError("Tavily needs TAVILY_API_KEY.")
        self._key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def spec(self) -> ToolSpec:
        return TAVILY_SPEC

    def search(self, query: str, *, limit: int = 3) -> list[Evidence]:
        payload = {"query": query, "max_results": max(1, min(limit, MAX_RESULTS))}
        try:
            response = self._client.post(
                "https://api.tavily.com/search",
                json=payload,
                headers={"Authorization": f"Bearer {self._key}", "User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(redact(f"Tavily search failed: {exc}", [self._key])) from exc
        return [
            Evidence.create(
                source=str(item.get("url", "tavily")),
                title=str(item.get("title", "")),
                excerpt=str(item.get("content", ""))[:8000],
            )
            for item in results
            if str(item.get("content", "")).strip()
        ]
