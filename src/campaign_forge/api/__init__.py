"""The HTTP API (Session 7, ADR-034). Requires the ``[api]`` extra."""

from __future__ import annotations

from .app import API_KEY_HEADER, create_app

__all__ = ["API_KEY_HEADER", "create_app"]
