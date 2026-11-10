"""Server retry hints: ``Retry-After`` headers and "try again in ..." messages."""

from __future__ import annotations

import re

_DURATION_HINT_RE = re.compile(
    r"(?:try again|retry)\s+in\s+((?:\d+(?:\.\d+)?\s*(?:ms|h|m|s)\s*)+)", re.IGNORECASE
)
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", re.IGNORECASE)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration_hint(message: str) -> float | None:
    """Parse "try again in" durations such as ``5.66s``, ``7m12.5s``, ``1h2m`` or ``250ms``."""
    match = _DURATION_HINT_RE.search(message)
    if not match:
        return None
    total = 0.0
    for amount, unit in _DURATION_PART_RE.findall(match.group(1)):
        total += float(amount) * _UNIT_SECONDS[unit.lower()]
    return total


def retry_after_seconds(exc: BaseException | None) -> float | None:
    """Extract a server-suggested retry delay from an error, if any.

    Order of preference:

    1. the ``retry-after-ms`` header
    2. the ``retry-after`` header, in seconds (HTTP-date values are ignored)
    3. the provider's message (e.g. Groq's "Please try again in 5.66s" or "in 7m12.5s")
    """
    if exc is None:
        return None

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        millis = headers.get("retry-after-ms")
        if millis:
            try:
                return float(millis) / 1000.0
            except ValueError:
                pass
        header_value = headers.get("retry-after")
        if header_value:
            try:
                return float(header_value)
            except ValueError:
                pass

    message = str(getattr(exc, "message", "") or exc)
    return parse_duration_hint(message)
