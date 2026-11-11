"""Everything an interface can do with Campaign Forge, behind one object.

The API maps HTTP to these methods and back; nothing else. Every method that
touches runs, workflows or memory takes the caller's scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ..config import Settings
from ..core.agent import AgentSpec
from ..domain.scope import Scope
from ..exceptions import (
    CampaignForgeError,
    ConfigurationError,
    NotFoundError,
    RegistryError,
    RunNotFoundError,
)
from ..memory.base import MemoryStore
from ..memory.sqlite import SqliteMemoryStore
from ..orchestration.engine import WorkflowEngine
from ..orchestration.factory import build_engine
from ..orchestration.state import RunRecord
from ..orchestration.templates import BUILTIN_TEMPLATES
from ..providers.profiles import BUILTIN_PROFILES
from .catalog import CatalogEntry, WorkflowCatalog
from .plans import customize, tighten
from .runs import RunService


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    display_name: str
    free_tier: bool
    configured: bool
    allowed: bool
    is_global: bool
    default_model: str | None
    models: Mapping[str, str]
    data_use_notice: str


@dataclass(frozen=True)
class Execution:
    """A run as an interface sees it: the checkpoint (if saved yet) plus live state."""

    run_id: str
    record: RunRecord | None
    running: bool
    error: str | None


@dataclass(frozen=True)
class RunOptions:
    """What a caller may change about a plan: skip optional steps, spend less."""

    skip_steps: tuple[str, ...] = ()
    max_llm_calls: int | None = None
    max_total_tokens: int | None = None
    max_wall_seconds: float | None = None


class Platform:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: WorkflowEngine | None = None,
        memory: MemoryStore | None = None,
        background: bool = True,
    ) -> None:
        self.settings = settings
        self.engine = engine or build_engine(settings)
        self._own_memory: SqliteMemoryStore | None = None
        if memory is None:
            self._own_memory = SqliteMemoryStore(
                settings.memory_db, secrets=settings.secret_values()
            )
            memory = self._own_memory
        self.memory = memory
        self.runs = RunService(
            self.engine,
            memory,
            background=background,
            history=settings.memory_history,
            max_active=settings.api_max_active_runs,
        )
        self.workflows = WorkflowCatalog(self.engine.registry)

    @property
    def simulated(self) -> bool:
        return self.engine.router.global_provider == "fake"

    # ------------------------------------------------------------------ #
    # Agents
    # ------------------------------------------------------------------ #
    def agents(self) -> list[AgentSpec]:
        return self.engine.registry.specs()

    def agent(self, agent_id: str) -> AgentSpec:
        try:
            return self.engine.registry.spec(agent_id)
        except RegistryError:
            raise NotFoundError(f"Agent {agent_id!r} not found.") from None

    def agent_enabled(self, agent_id: str) -> bool:
        return self.engine.registry.is_enabled(agent_id)

    # ------------------------------------------------------------------ #
    # Workflows and runs
    # ------------------------------------------------------------------ #
    def workflow(self, scope: Scope, workflow_id: str) -> CatalogEntry:
        return self.workflows.get(scope, workflow_id)

    def list_workflows(self, scope: Scope) -> list[CatalogEntry]:
        return self.workflows.entries(scope)

    def save_workflow(self, scope: Scope, definition: Any) -> CatalogEntry:
        return self.workflows.save(scope, definition)

    def run_workflow(
        self,
        scope: Scope,
        workflow_id: str,
        inputs: Mapping[str, BaseModel],
        options: RunOptions = RunOptions(),
    ) -> str:
        """Start ``workflow_id`` (built-in or the caller's own) with the caller's options.

        Raises:
            NotFoundError: unknown workflow.
            ConfigurationError: a step that cannot be skipped, or an input the
                workflow does not take.
            WorkflowValidationError: the plan is invalid.
            CapacityError: too many runs are executing.
        """
        definition = self.workflows.get(scope, workflow_id).definition
        accepted = set(definition.inputs) | set(definition.optional_inputs)
        unknown = sorted(set(inputs) - accepted)
        if unknown:
            raise ConfigurationError(
                f"Workflow {workflow_id!r} does not take: {', '.join(unknown)}."
            )
        limits = tighten(
            definition.limits,
            max_llm_calls=options.max_llm_calls,
            max_total_tokens=options.max_total_tokens,
            max_wall_seconds=options.max_wall_seconds,
        )
        plan = customize(definition, skip=options.skip_steps, limits=limits)
        return self.runs.start(plan, inputs, scope=scope)

    def start_campaign(
        self,
        scope: Scope,
        workflow_id: str,
        inputs: Mapping[str, BaseModel],
        options: RunOptions = RunOptions(),
    ) -> str:
        """A campaign is a built-in template run on a brief."""
        if workflow_id not in BUILTIN_TEMPLATES:
            known = ", ".join(sorted(BUILTIN_TEMPLATES))
            raise NotFoundError(f"Unknown campaign workflow {workflow_id!r}. Known: {known}.")
        return self.run_workflow(scope, workflow_id, inputs, options)

    def execution(self, scope: Scope, run_id: str) -> Execution:
        """The run in ``scope``; a run still starting is visible only to its own scope.

        Raises:
            RunNotFoundError: unknown in this scope.
        """
        record = self.runs.record(run_id, scope=scope)
        running = self.runs.is_running(run_id, scope=scope)
        error = self.runs.error(run_id, scope=scope)
        if record is None and not running and error is None:
            raise RunNotFoundError(f"Run {run_id!r} not found.")
        return Execution(run_id, record, running, error)

    def executions(self, scope: Scope) -> list[RunRecord]:
        return self.runs.runs(scope=scope)

    def resume(self, scope: Scope, run_id: str) -> None:
        self.runs.resume(run_id, scope=scope)

    def cancel(self, scope: Scope, run_id: str) -> RunRecord:
        return self.runs.cancel(run_id, scope=scope)

    def approve(
        self, scope: Scope, run_id: str, step_id: str, *, approved: bool, comment: str = ""
    ) -> None:
        self.runs.approve(run_id, step_id, scope=scope, approved=approved, comment=comment)

    # ------------------------------------------------------------------ #
    # Providers and status
    # ------------------------------------------------------------------ #
    def providers(self) -> list[ProviderInfo]:
        router = self.engine.router
        infos = []
        for profile in BUILTIN_PROFILES.values():
            try:
                router.ensure_allowed(profile)
                allowed = True
            except CampaignForgeError:
                allowed = False
            configured = profile.api_key_field is None or bool(
                self.settings.secret(profile.api_key_field)
            )
            infos.append(
                ProviderInfo(
                    name=profile.name,
                    display_name=profile.display_name,
                    free_tier=profile.free_tier,
                    configured=configured,
                    allowed=allowed,
                    is_global=profile.name == router.global_provider,
                    default_model=profile.default_model,
                    models=dict(profile.model_aliases),
                    data_use_notice=profile.data_use_notice,
                )
            )
        return infos

    def status(self) -> dict[str, Any]:
        settings = self.settings
        return {
            "simulated": self.simulated,
            "provider": self.engine.router.global_provider,
            "free_only": settings.free_only,
            "run_store": settings.run_store,
            # In simulated mode research uses the offline search, whatever is configured.
            "research_tools": (
                ["offline_search"] if self.simulated else list(settings.research_tool_names)
            ),
            "active_runs": self.runs.active_count(),
            "max_active_runs": settings.api_max_active_runs,
        }

    def close(self, timeout: float = 5.0) -> None:
        self.runs.wait(timeout)
        if self._own_memory is not None:
            self._own_memory.close()
