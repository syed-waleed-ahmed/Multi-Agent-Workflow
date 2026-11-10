"""Secret redaction for log records and user-facing error messages.

Two complementary mechanisms:

* **Known secret values** (the API keys actually configured) are replaced
  verbatim. This covers keys without a recognisable prefix.
* **Key-shaped patterns** for common providers are replaced even when the value
  is not configured locally, e.g. a key echoed back inside a provider's error
  message.

Limitation: tracebacks rendered by a logging handler are formatted after
filtering and are not redacted. The package never logs locals in tracebacks, and
keys are held in ``SecretStr`` fields so they do not appear in object reprs.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterable

REDACTED = "[REDACTED]"
_MIN_SECRET_LEN = 8

_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),  # Groq
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI-style
    re.compile(r"\btvly-[A-Za-z0-9_\-]{16,}"),  # Tavily
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),  # Authorization headers
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Return ``text`` with known secrets and key-shaped strings replaced."""
    result = text
    for secret in sorted({s for s in secrets if len(s) >= _MIN_SECRET_LEN}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    for pattern in _KEY_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


class RedactingFilter(logging.Filter):
    """A logging filter that redacts secrets from each record's rendered message."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._secrets: frozenset[str] = frozenset()
        self.add_secrets(secrets)

    def add_secrets(self, secrets: Iterable[str]) -> None:
        """Register additional secret values to redact."""
        new = {s for s in secrets if s and len(s) >= _MIN_SECRET_LEN}
        with self._lock:
            self._secrets = self._secrets | new

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message, self._secrets)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
