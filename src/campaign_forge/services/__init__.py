"""The application service layer (Session 7, ADR-034): one place for what interfaces do.

* :class:`RunService` starts, tracks and controls runs (background threads, run
  history, a cap on active runs). :class:`RunSlots` caps runs across services.
* :mod:`.live` holds the public demo's live-mode caps (ADR-037).
* :class:`Platform` covers agents, workflows (built-in and per-tenant custom),
  runs, providers and status. The API is a thin HTTP mapping over it, and an
  architecture rule keeps it that way.
"""

from __future__ import annotations

from .catalog import MAX_CUSTOM_WORKFLOWS, CatalogEntry, WorkflowCatalog
from .live import LiveLimits, LiveQuota
from .plans import customize, tighten
from .platform import Execution, Platform, ProviderInfo, RunOptions
from .runs import RunService, RunSlots

__all__ = [
    "MAX_CUSTOM_WORKFLOWS",
    "CatalogEntry",
    "Execution",
    "LiveLimits",
    "LiveQuota",
    "Platform",
    "ProviderInfo",
    "RunOptions",
    "RunService",
    "RunSlots",
    "WorkflowCatalog",
    "customize",
    "tighten",
]
