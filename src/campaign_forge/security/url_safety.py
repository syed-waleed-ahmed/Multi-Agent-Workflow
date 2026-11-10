"""URL safety for user-supplied and model-proposed URLs (SSRF defence, docs/SECURITY.md T4).

A URL may be fetched only if all of these hold:

* the scheme is ``http`` or ``https``;
* it carries no embedded credentials;
* the port is 80 or 443;
* **every** address its host resolves to is a public unicast address.

Literal IPs in any notation are covered, because the host is resolved and the
resolved addresses are what get checked. That includes decimal (``2130706433``),
IPv6 and IPv4-mapped IPv6 forms.

The fetcher re-checks each redirect target. Known limitation (documented in
SECURITY.md): a DNS answer can change between this check and the connection
(DNS rebinding). Session 9 hardens this.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from ..exceptions import ToolError

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

Resolver = Callable[[str, int], Iterable[str]]


class UnsafeURLError(ToolError):
    """Raised when a URL must not be fetched."""


def _system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def check_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Validate ``url`` for fetching and return it unchanged.

    Raises:
        UnsafeURLError: with a reason a user can act on.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError(f"Invalid URL: {exc}") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Only http and https URLs can be fetched, not {parts.scheme!r}.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not fetched.")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("The URL has no host.")
    effective_port = port or (443 if parts.scheme.lower() == "https" else 80)
    if effective_port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"Port {effective_port} is not allowed; use 80 or 443.")
    try:
        addresses = list((resolver or _system_resolver)(host, effective_port))
    except (OSError, UnicodeError) as exc:
        raise UnsafeURLError(f"Cannot resolve {host!r}: {exc}") from exc
    if not addresses:
        raise UnsafeURLError(f"{host!r} did not resolve to any address.")
    for address in addresses:
        try:
            public = _is_public(address)
        except ValueError as exc:
            raise UnsafeURLError(f"{host!r} resolved to an invalid address.") from exc
        if not public:
            raise UnsafeURLError(
                f"{host!r} resolves to a non-public address ({address}); it is not fetched."
            )
    return url
