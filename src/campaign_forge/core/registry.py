"""The agent registry: register, discover, look up and enable or disable agents.

New agents are added without touching the orchestrator, in one of two ways:

* **in-package:** decorate the class with ``@registry.register``;
* **third-party:** expose an entry point in the ``campaign_forge.agents`` group.
  It may name either an agent class, or a ``register(registry)`` hook that
  registers several agents.

The registry is an explicit object, not a hidden global, so tests and embedding
applications can hold independent registries.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import TypeVar

from ..exceptions import RegistryError
from ..logging_config import get_logger
from .agent import Agent, AgentCategory, AgentSpec

ENTRY_POINT_GROUP = "campaign_forge.agents"

AgentClassT = TypeVar("AgentClassT", bound=type)
EntryPointLoader = Callable[[str], Iterable[EntryPoint]]


def _load_group(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


class AgentRegistry:
    """A thread-safe catalog of agent classes keyed by ``spec.id``."""

    def __init__(self) -> None:
        self._agents: dict[str, type[Agent]] = {}
        self._disabled: set[str] = set()
        self._lock = threading.RLock()
        self._log = get_logger("registry")

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, agent_cls: AgentClassT) -> AgentClassT:
        """Register an agent class (usable as a decorator).

        Raises:
            RegistryError: if the class has no valid ``spec``/``run``, or another
                class already uses the same id.
        """
        spec = getattr(agent_cls, "spec", None)
        if not isinstance(spec, AgentSpec):
            raise RegistryError(
                f"{agent_cls.__name__} must define a class attribute 'spec' of type AgentSpec."
            )
        if not callable(getattr(agent_cls, "run", None)):
            raise RegistryError(f"{agent_cls.__name__} must implement run(inputs, ctx).")
        with self._lock:
            existing = self._agents.get(spec.id)
            if existing is not None and existing is not agent_cls:
                raise RegistryError(
                    f"Agent id {spec.id!r} is already registered by {existing.__name__}."
                )
            self._agents[spec.id] = agent_cls
        return agent_cls

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._require(agent_id)
            del self._agents[agent_id]
            self._disabled.discard(agent_id)

    def load_entry_points(
        self,
        group: str = ENTRY_POINT_GROUP,
        *,
        strict: bool = True,
        loader: EntryPointLoader = _load_group,
    ) -> list[str]:
        """Register agents advertised by installed packages.

        Args:
            group: Entry-point group to scan.
            strict: Raise on a broken plugin (default). If ``False``, log the
                error and skip the plugin.
            loader: Injectable for tests; defaults to ``importlib.metadata``.

        Returns:
            The entry-point names that loaded successfully.
        """
        loaded: list[str] = []
        for entry_point in loader(group):
            try:
                target = entry_point.load()
                if isinstance(target, type):
                    self.register(target)
                elif callable(target):
                    target(self)
                else:
                    raise RegistryError("entry point is neither an agent class nor a hook")
            except Exception as exc:
                message = f"Failed to load agent plugin {entry_point.name!r}: {exc}"
                if strict:
                    raise RegistryError(message) from exc
                self._log.error(message)
                continue
            loaded.append(entry_point.name)
        return loaded

    # ------------------------------------------------------------------ #
    # Lookup
    # ------------------------------------------------------------------ #
    def get(self, agent_id: str) -> type[Agent]:
        with self._lock:
            return self._require(agent_id)

    def spec(self, agent_id: str) -> AgentSpec:
        return self.get(agent_id).spec

    def create(self, agent_id: str) -> Agent:
        """Instantiate an enabled agent."""
        with self._lock:
            agent_cls = self._require(agent_id)
            if agent_id in self._disabled:
                raise RegistryError(f"Agent {agent_id!r} is disabled.")
        return agent_cls()

    def specs(
        self, *, category: AgentCategory | None = None, include_disabled: bool = True
    ) -> list[AgentSpec]:
        """All registered specs, sorted by id."""
        with self._lock:
            items = [
                cls.spec
                for agent_id, cls in self._agents.items()
                if include_disabled or agent_id not in self._disabled
            ]
        return sorted(
            (s for s in items if category is None or s.category is category), key=lambda s: s.id
        )

    # ------------------------------------------------------------------ #
    # Enable / disable (Agent Marketplace)
    # ------------------------------------------------------------------ #
    def disable(self, agent_id: str) -> None:
        with self._lock:
            self._require(agent_id)
            self._disabled.add(agent_id)

    def enable(self, agent_id: str) -> None:
        with self._lock:
            self._require(agent_id)
            self._disabled.discard(agent_id)

    def is_enabled(self, agent_id: str) -> bool:
        with self._lock:
            self._require(agent_id)
            return agent_id not in self._disabled

    def __contains__(self, agent_id: object) -> bool:
        with self._lock:
            return agent_id in self._agents

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def _require(self, agent_id: str) -> type[Agent]:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise RegistryError(f"Unknown agent {agent_id!r}.") from None
