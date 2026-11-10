"""Centralised logging configuration.

Uses :class:`rich.logging.RichHandler` for readable, colourised console output.
Library code obtains loggers via :func:`get_logger` and never configures
handlers itself, so importing the package stays quiet until an application
(the CLI) explicitly opts in with :func:`configure_logging`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from rich.console import Console
from rich.logging import RichHandler

from .security.redaction import RedactingFilter

_LOGGER_NAMESPACE = "campaign_forge"
_configured = False
_redactor = RedactingFilter()


def configure_logging(level: str = "INFO", *, secrets: Iterable[str] = ()) -> None:
    """Configure the package logger with a Rich console handler and secret redaction.

    Idempotent: calling it again updates the level and adds any new secrets to
    redact. Only the ``campaign_forge`` logger is touched, so we never hijack the
    root logger of a host application that imports this package as a library.

    Args:
        level: Log level name.
        secrets: Secret values (e.g. ``Settings.secret_values()``) to redact
            verbatim. Key-shaped strings are redacted regardless.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(level.upper())
    logger.propagate = False
    _redactor.add_secrets(secrets)

    if not _configured:
        # Logs go to stderr so that stdout stays reserved for data (e.g. --json).
        handler = RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            show_path=False,
            markup=False,
            omit_repeated_times=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        handler.addFilter(_redactor)
        logger.addHandler(handler)
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``campaign_forge`` namespace."""
    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{name}")
