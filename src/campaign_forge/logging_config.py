"""Centralised logging configuration.

Uses :class:`rich.logging.RichHandler` for readable, colourised console output.
Library code obtains loggers via :func:`get_logger` and never configures
handlers itself, so importing the package stays quiet until an application
(the CLI) explicitly opts in with :func:`configure_logging`.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

_LOGGER_NAMESPACE = "campaign_forge"
_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Configure the package logger with a Rich console handler.

    Idempotent: calling it more than once only updates the level. Only the
    ``campaign_forge`` logger is touched, so we never hijack the root logger of
    a host application that imports this package as a library.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(level.upper())
    logger.propagate = False

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
        logger.addHandler(handler)
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``campaign_forge`` namespace."""
    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{name}")
