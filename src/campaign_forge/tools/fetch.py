"""A guarded HTTP fetcher for **user-supplied** URLs (never model-proposed ones).

Every request is defended:

* **SSRF:** the URL safety check runs on the first URL **and on every redirect
  target**, and the number of redirects is capped.
* **robots.txt** is honoured, following RFC 9309: 4xx means no restrictions;
  5xx or an unreachable server means fetch nothing.
* **Bounded responses:** a timeout, a byte cap enforced while streaming, and a
  content-type allowlist.
* **No credentials:** cookies and auth headers are never sent.
"""

from __future__ import annotations

import threading
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..exceptions import ToolError
from ..security.url_safety import Resolver, check_url
from .html_text import html_to_text

USER_AGENT = "CampaignForge/2.0 (+https://github.com/syed-waleed-ahmed/Multi-Agent-Workflow)"
ROBOTS_AGENT = "CampaignForge"
ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json"}
)
DEFAULT_MAX_BYTES = 1_000_000


class SafeFetcher:
    """Fetch public pages under the rules above. Thread-safe."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        resolver: Resolver | None = None,
        timeout: float = 10.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = 3,
        respect_robots: bool = True,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._resolver = resolver
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> tuple[str, str, str]:
        """Return ``(final_url, content_type, text)`` for a public URL.

        Raises:
            ToolError: (including ``UnsafeURLError``) if the URL may not be fetched
                or the response breaks a limit.
        """
        current = url
        for _ in range(self._max_redirects + 1):
            check_url(current, resolver=self._resolver)
            if self._respect_robots and not self._robots_allow(current):
                raise ToolError(f"robots.txt disallows fetching {current}.")
            try:
                with self._client.stream(
                    "GET", current, headers={"User-Agent": USER_AGENT}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ToolError(f"Redirect without a location from {current}.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise ToolError(f"{current} returned HTTP {response.status_code}.")
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if content_type not in ALLOWED_CONTENT_TYPES:
                        raise ToolError(f"{current} has unsupported content type {content_type!r}.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise ToolError(f"{current} is larger than {self._max_bytes} bytes.")
                    text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    return current, content_type, text
            except httpx.HTTPError as exc:
                raise ToolError(f"Could not fetch {current}: {type(exc).__name__}.") from exc
        raise ToolError(f"Too many redirects starting from {url}.")

    def fetch_text(self, url: str) -> tuple[str, str, str]:
        """Return ``(final_url, title, readable_text)``."""
        final_url, content_type, body = self.get(url)
        if content_type in ("text/html", "application/xhtml+xml"):
            title, text = html_to_text(body)
        else:
            title, text = "", body.strip()
        if not text:
            raise ToolError(f"{final_url} contains no readable text.")
        return final_url, title or final_url, text

    def _robots_allow(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            known = origin in self._robots
            parser = self._robots.get(origin)
        if not known:
            parser = self._load_robots(origin)
            with self._lock:
                self._robots[origin] = parser
        return True if parser is None else parser.can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """``None`` means "no restrictions"; a parser that disallows everything means "none"."""
        parser = RobotFileParser()
        try:
            response = self._client.get(f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError:
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        if 400 <= response.status_code < 500:
            return None
        if response.status_code >= 500:
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        parser.parse(response.text.splitlines())
        return parser
