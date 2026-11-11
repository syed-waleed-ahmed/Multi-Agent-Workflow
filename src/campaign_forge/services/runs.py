"""Starting, tracking and controlling runs: the one place background execution lives.

Both the web app (local mode) and the API run workflows in background threads
inside one process (ADR-009: threads, not asyncio). :class:`RunService` owns
those threads, records errors that happen outside the engine's own record,
caps how many runs execute at once, and adds completed runs to campaign
history. Every call takes the caller's scope, so one tenant can never start,
read or control another tenant's run.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Mapping

from pydantic import BaseModel

from ..domain.artifacts import Artifact
from ..domain.scope import Scope
from ..exceptions import CampaignForgeError, CapacityError, MemoryStoreError, RunNotFoundError
from ..memory.base import MemoryStore
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.engine import RunResult, WorkflowEngine, new_run_id
from ..orchestration.history import remember_run
from ..orchestration.state import RunRecord


class RunService:
    """Runs workflows synchronously or in background threads.

    Args:
        background: run in a thread and return at once (the API, the app's local
            mode), or finish before returning (the app's demo mode, which is instant).
        history: remember completed runs as campaign documents in ``memory``.
        max_active: the most background runs executing at once (``None``: no cap).
    """

    def __init__(
        self,
        engine: WorkflowEngine,
        memory: MemoryStore,
        *,
        background: bool,
        history: bool = True,
        max_active: int | None = None,
    ) -> None:
        self.engine = engine
        self.memory = memory
        self.background = background
        self.history = history
        self.max_active = max_active
        self._threads: dict[str, threading.Thread] = {}
        self._errors: dict[str, str] = {}
        self._owners: dict[str, Scope] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    def start(
        self,
        definition: WorkflowDefinition,
        inputs: Mapping[str, BaseModel | Artifact],
        *,
        scope: Scope,
    ) -> str:
        """Validate, then start a run. Returns its id (a background run may still execute).

        Raises:
            WorkflowValidationError: if the definition is invalid; no run is created.
            CapacityError: if ``max_active`` background runs are already executing.
        """
        self.engine.validate(definition)
        run_id = new_run_id()
        self._dispatch(
            run_id,
            scope,
            lambda: self.engine.start(definition, inputs, scope=scope, run_id=run_id),
        )
        return run_id

    def resume(self, run_id: str, *, scope: Scope) -> None:
        self.engine.get(run_id, scope=scope)  # 404 before any thread starts
        self._dispatch(run_id, scope, lambda: self.engine.resume(run_id, scope=scope))

    def approve(
        self, run_id: str, step_id: str, *, scope: Scope, approved: bool, comment: str = ""
    ) -> None:
        self.engine.get(run_id, scope=scope)
        self._dispatch(
            run_id,
            scope,
            lambda: self.engine.approve(
                run_id, step_id, scope=scope, approved=approved, comment=comment or None
            ),
        )

    def cancel(self, run_id: str, *, scope: Scope) -> RunRecord:
        return self.engine.cancel(run_id, scope=scope)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def record(self, run_id: str, *, scope: Scope) -> RunRecord | None:
        """The run's latest checkpoint, or ``None`` before its first save (or if unknown)."""
        try:
            return self.engine.get(run_id, scope=scope)
        except RunNotFoundError:
            return None

    def runs(self, *, scope: Scope) -> list[RunRecord]:
        return self.engine.list_runs(scope=scope)

    def is_running(self, run_id: str, *, scope: Scope | None = None) -> bool:
        """Whether a background run executes now (only for its own scope, when given)."""
        with self._lock:
            thread = self._threads.get(run_id)
            owner = self._owners.get(run_id)
        if scope is not None and owner != scope:
            return False
        return thread is not None and thread.is_alive()

    def error(self, run_id: str, *, scope: Scope | None = None) -> str | None:
        """An error that stopped a background run outside the engine's own record."""
        with self._lock:
            if scope is not None and self._owners.get(run_id) != scope:
                return None
            return self._errors.get(run_id)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._threads.values() if t.is_alive())

    def wait(self, timeout: float = 30.0) -> bool:
        """Wait for every background run to finish (tests and shutdown). ``True`` if all did."""
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout)
        return not any(t.is_alive() for t in threads)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _dispatch(self, run_id: str, scope: Scope, work: Callable[[], RunResult]) -> None:
        if not self.background:
            self._remember(work().record)
            return

        def target() -> None:
            try:
                self._remember(work().record)
            except CampaignForgeError as exc:
                with self._lock:
                    self._errors[run_id] = str(exc)
            except Exception as exc:  # noqa: BLE001 - a worker thread must report, not die silently
                with self._lock:
                    self._errors[run_id] = f"Unexpected {type(exc).__name__}: {exc}"

        thread = threading.Thread(target=target, name=f"campaign-forge-{run_id}", daemon=True)
        with self._lock:
            active = sum(1 for t in self._threads.values() if t.is_alive())
            if self.max_active is not None and active >= self.max_active:
                raise CapacityError(
                    f"{active} runs are already executing (the limit is {self.max_active}). "
                    "Retry when one finishes."
                )
            self._errors.pop(run_id, None)
            self._threads[run_id] = thread
            self._owners[run_id] = scope
            # Forget finished threads so the registry stays bounded.
            for done in [k for k, t in self._threads.items() if not t.is_alive() and k != run_id]:
                del self._threads[done]
        thread.start()

    def _remember(self, record: RunRecord) -> None:
        if not self.history:
            return
        # History is a convenience: a full memory never fails a run.
        with contextlib.suppress(MemoryStoreError):
            remember_run(self.memory, record)
