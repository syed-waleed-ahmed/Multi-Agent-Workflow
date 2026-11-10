"""Research tool contracts, the toolbox, and the run-level evidence store.

Tools are **search** tools: given a query, they return :class:`Evidence`. Agents
never call a tool directly, and a model never names a URL to fetch. An agent's
code passes model-proposed *queries* to ``RunContext.search``, which:

* enforces the agent's ``allowed_tools``;
* charges the run budget;
* records every returned evidence item.

URL fetching happens only for URLs the user supplies before a run (ADR-029).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.provenance import Evidence


class ToolSpec(BaseModel):
    """What a tool is, for allowlists, the UI and documentation."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str
    network: bool = True
    requires_key: bool = False


@runtime_checkable
class SearchTool(Protocol):
    """Returns evidence for a query. Implementations must be safe to call from threads."""

    @property
    def spec(self) -> ToolSpec: ...

    def search(self, query: str, *, limit: int = 3) -> list[Evidence]: ...


class ToolBox:
    """The search tools available to a run (built from configuration)."""

    def __init__(self, tools: Iterable[SearchTool] = ()) -> None:
        self._tools = {tool.spec.id: tool for tool in tools}

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> list[ToolSpec]:
        return [self._tools[i].spec for i in self.ids]

    def allowed(self, allowed_tools: Iterable[str]) -> list[SearchTool]:
        """Configured tools that also appear in an agent's allowlist, in allowlist order."""
        return [self._tools[t] for t in allowed_tools if t in self._tools]

    def __len__(self) -> int:
        return len(self._tools)


class EvidenceStore:
    """Thread-safe record of every evidence item retrieved during a run."""

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._lock = threading.Lock()

    def add(self, items: Iterable[Evidence]) -> None:
        with self._lock:
            for item in items:
                self._items.setdefault(item.id, item)

    def get(self, evidence_id: str) -> Evidence | None:
        with self._lock:
            return self._items.get(evidence_id)

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
