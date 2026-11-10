"""SSRF defence and the safe fetcher (docs/SECURITY.md T4). No real network is used."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from campaign_forge.exceptions import ToolError
from campaign_forge.security.url_safety import UnsafeURLError, check_url
from campaign_forge.tools.fetch import SafeFetcher
from campaign_forge.tools.html_text import html_to_text

PUBLIC = "93.184.216.34"


def _resolver(mapping: dict[str, str]) -> Callable[[str, int], list[str]]:
    return lambda host, port: [mapping.get(host, PUBLIC)]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::ffff:10.0.0.1]/",
        "http://10.1.2.3/",
        "http://192.168.0.1/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://user:pw@example.com/",
        "http://example.com:8080/",
        "http:///nohost",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        check_url(
            url,
            resolver=lambda host, port: (
                [host.strip("[]")] if ":" in host or host[0].isdigit() else [PUBLIC]
            ),
        )


def test_hostnames_resolving_to_private_addresses_are_refused() -> None:
    with pytest.raises(UnsafeURLError, match="non-public address"):
        check_url("http://intranet.test/", resolver=_resolver({"intranet.test": "10.0.0.5"}))
    with pytest.raises(UnsafeURLError, match="did not resolve"):
        check_url("http://nothing.test/", resolver=lambda host, port: [])
    with pytest.raises(UnsafeURLError, match="Cannot resolve"):
        check_url(
            "http://broken.test/", resolver=lambda host, port: (_ for _ in ()).throw(OSError("dns"))
        )
    with pytest.raises(UnsafeURLError, match="invalid address"):
        check_url("http://odd.test/", resolver=lambda host, port: ["not-an-ip"])
    with pytest.raises(UnsafeURLError, match="Invalid URL"):
        check_url("http://example.com:99999/")
    assert check_url("https://good.test/a", resolver=_resolver({})) == "https://good.test/a"


def _fetcher(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> SafeFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SafeFetcher(client=client, resolver=_resolver(kwargs.pop("hosts", {})), **kwargs)  # type: ignore[arg-type]


def _html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, text=body, headers={"content-type": "text/html; charset=utf-8", **headers}
    )


def test_fetch_extracts_title_and_text_and_honours_robots_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.headers["user-agent"].startswith("CampaignForge/")
        return _html(
            "<html><head><title> Bottle  Review </title><script>x()</script></head>"
            "<body><nav>menu</nav><h1>Great</h1><p>Keeps   drinks cold.</p></body></html>"
        )

    url, title, text = _fetcher(handler).fetch_text("https://site.test/page")
    assert (url, title) == ("https://site.test/page", "Bottle Review")
    assert "Keeps drinks cold." in text and "x()" not in text and "menu" not in text


def test_redirects_are_rechecked_and_capped() -> None:
    def to_private(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})

    with pytest.raises(UnsafeURLError, match="non-public"):
        _fetcher(to_private, hosts={"internal.test": "10.0.0.9"}).get("https://site.test/")

    def loop(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(ToolError, match="Too many redirects"):
        _fetcher(loop, max_redirects=2).get("https://site.test/")

    def no_location(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(302)

    with pytest.raises(ToolError, match="without a location"):
        _fetcher(no_location).get("https://site.test/")


@pytest.mark.parametrize(
    "robots_status,robots_body,allowed",
    [
        (200, "User-agent: *\nDisallow: /private", False),
        (500, "", False),
        (200, "User-agent: *\nAllow: /", True),
    ],
)
def test_robots_txt_semantics(robots_status: int, robots_body: str, allowed: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots_body)
        return _html("<p>secret page</p>")

    fetcher = _fetcher(handler)
    if allowed:
        assert fetcher.get("https://site.test/private/x")[2] == "<p>secret page</p>"
    else:
        with pytest.raises(ToolError, match="robots.txt disallows"):
            fetcher.get("https://site.test/private/x")


def test_unreachable_robots_means_no_fetching() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("down")
        return _html("<p>x</p>")

    with pytest.raises(ToolError, match="robots.txt disallows"):
        _fetcher(handler).get("https://site.test/")


def test_size_type_status_and_network_limits() -> None:
    def big(request: httpx.Request) -> httpx.Response:
        return _html("x" * 5000)

    with pytest.raises(ToolError, match="larger than 1000 bytes"):
        _fetcher(big, respect_robots=False, max_bytes=1000).get("https://site.test/")

    def binary(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00", headers={"content-type": "application/pdf"})

    with pytest.raises(ToolError, match="unsupported content type"):
        _fetcher(binary, respect_robots=False).get("https://site.test/")

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(ToolError, match="HTTP 404"):
        _fetcher(missing, respect_robots=False).get("https://site.test/")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(ToolError, match="Could not fetch"):
        _fetcher(down, respect_robots=False).get("https://site.test/")

    def blank(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="   ", headers={"content-type": "text/plain"})

    with pytest.raises(ToolError, match="no readable text"):
        _fetcher(blank, respect_robots=False).fetch_text("https://site.test/")

    def plain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="plain notes", headers={"content-type": "text/plain"})

    assert (
        _fetcher(plain, respect_robots=False).fetch_text("https://site.test/n.txt")[2]
        == "plain notes"
    )


def test_html_to_text_handles_structure() -> None:
    title, text = html_to_text(
        "<title>T</title><style>.a{}</style><ul><li>one</li><li>two</li></ul><footer>f</footer>"
    )
    assert title == "T"
    assert text.splitlines() == ["one", "", "two"] or ("one" in text and "two" in text)
    assert ".a{}" not in text and "f" not in text.split()
