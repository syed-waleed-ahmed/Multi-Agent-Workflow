"""The app's per-browser-session state, with no Streamlit dependency.

Two modes (``CF_UI_MODE``):

* **demo** (the default, and the public deployment): every agent is answered by
  the deterministic offline responder, and every output is labeled SIMULATED.
  Runs, brands and notes live in this session's memory only. Nothing is written
  on the server (ADR-008), so a visitor's data ends with their browser session.
* **local**: your configured provider, plus the run store and memory database
  the CLI uses (``CF_RUNS_DIR`` or SQLite, ``CF_MEMORY_DB``). Runs execute in
  a background thread, so the page stays responsive while a free tier waits
  out its rate limits.

The Streamlit pages only read and call this object. Every engine and memory
call goes through it with the session's scope.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..config import Settings
from ..domain.artifacts import Artifact
from ..domain.brand import BrandProfile
from ..domain.scope import Scope
from ..evaluation.offline import OfflineSearch, offline_responder
from ..exceptions import CampaignForgeError, MemoryStoreError, RunNotFoundError
from ..memory.sqlite import IN_MEMORY, SqliteMemoryStore
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.engine import RunResult, WorkflowEngine, new_run_id
from ..orchestration.history import remember_run
from ..orchestration.state import RunRecord
from ..orchestration.store import InMemoryRunStore
from ..orchestration.templates import default_registry
from ..providers.fake import FakeProvider
from ..providers.router import ProviderRouter
from ..tools import ToolBox

UIMode = Literal["demo", "local"]

#: The sample brand every demo session starts with, so Brand Memory has something to show.
SAMPLE_BRAND = BrandProfile(
    name="EcoSip",
    voice=(
        "Plain, warm and practical. EcoSip talks like a well-travelled friend who cares about "
        "the outdoors and never oversells."
    ),
    tone_words=["warm", "practical", "upbeat"],
    audience="Hikers and commuters who buy gear once and keep it for years.",
    values=["durability", "honesty", "low waste"],
    guidelines=["Lead with a concrete benefit", "Use short sentences", "Show real use"],
    avoid=["Hype and superlatives", "Claims the product cannot back up"],
    preferred_terms=["for years", "every day", "trail-ready"],
    banned_terms=["revolutionary", "game-changing", "cheap"],
    visual_style="Natural light, real people outdoors, uncluttered backgrounds.",
)


@dataclass
class AppSession:
    """Everything one browser session can see and do."""

    mode: UIMode
    settings: Settings
    engine: WorkflowEngine
    memory: SqliteMemoryStore
    scope: Scope = field(default_factory=Scope.local)
    _threads: dict[str, threading.Thread] = field(default_factory=dict, repr=False)
    _errors: dict[str, str] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def simulated(self) -> bool:
        return self.mode == "demo"

    @property
    def background(self) -> bool:
        """Demo runs finish in a moment; local runs may wait out rate limits for minutes."""
        return self.mode == "local"

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #
    def start(
        self, definition: WorkflowDefinition, inputs: Mapping[str, BaseModel | Artifact]
    ) -> str:
        """Validate, then start a run. Returns its id (the run may still be executing).

        Raises:
            WorkflowValidationError: if the definition or inputs are invalid. No run
                is created in that case.
        """
        self.engine.validate(definition)
        run_id = new_run_id()

        def work() -> RunResult:
            return self.engine.start(definition, inputs, scope=self.scope, run_id=run_id)

        if self.background:
            self._launch(run_id, work)
        else:
            self._remember(work().record)
        return run_id

    def resume(self, run_id: str) -> None:
        self._launch_or_run(run_id, lambda: self.engine.resume(run_id, scope=self.scope))

    def approve(self, run_id: str, step_id: str, *, approved: bool, comment: str = "") -> None:
        self._launch_or_run(
            run_id,
            lambda: self.engine.approve(
                run_id, step_id, scope=self.scope, approved=approved, comment=comment or None
            ),
        )

    def cancel(self, run_id: str) -> RunRecord:
        return self.engine.cancel(run_id, scope=self.scope)

    def record(self, run_id: str) -> RunRecord | None:
        """The run's latest checkpoint, or ``None`` before its first save."""
        try:
            return self.engine.get(run_id, scope=self.scope)
        except RunNotFoundError:
            return None

    def runs(self) -> list[RunRecord]:
        return self.engine.list_runs(scope=self.scope)

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        return thread is not None and thread.is_alive()

    def error(self, run_id: str) -> str | None:
        """An error that stopped a background run before or outside the engine's record."""
        with self._lock:
            return self._errors.get(run_id)

    def _launch_or_run(self, run_id: str, work: Callable[[], RunResult]) -> None:
        if self.background:
            self._launch(run_id, work)
        else:
            self._remember(work().record)

    def _launch(self, run_id: str, work: Callable[[], RunResult]) -> None:
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
            self._errors.pop(run_id, None)
            self._threads[run_id] = thread
        thread.start()

    def _remember(self, record: RunRecord) -> None:
        if not self.settings.memory_history:
            return
        # History is a convenience: a full memory never fails a run.
        with contextlib.suppress(MemoryStoreError):
            remember_run(self.memory, record)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self.memory.close()


def demo_session() -> AppSession:
    """A simulated session: offline responder, in-memory stores, a sample brand."""
    settings = Settings(_env_file=None, provider="fake", ui_mode="demo")
    router = ProviderRouter(settings)
    router.add_provider("fake", FakeProvider(offline_responder))
    engine = WorkflowEngine(
        default_registry(), router, store=InMemoryRunStore(), tools=ToolBox([OfflineSearch()])
    )
    memory = SqliteMemoryStore(IN_MEMORY)
    session = AppSession(mode="demo", settings=settings, engine=engine, memory=memory)
    memory.save_brand(session.scope, SAMPLE_BRAND)
    return session


def local_session(settings: Settings) -> AppSession:
    """Your configured provider, run store and memory database (the CLI's)."""
    from ..cli_runs import build_engine

    memory = SqliteMemoryStore(settings.memory_db, secrets=settings.secret_values())
    try:
        engine = build_engine(settings)
    except BaseException:
        memory.close()
        raise
    return AppSession(mode="local", settings=settings, engine=engine, memory=memory)


def build_session(settings: Settings | None = None) -> AppSession:
    """The session for ``CF_UI_MODE``. ``CF_PROVIDER=fake`` always means demo mode."""
    resolved = settings or Settings()
    if resolved.ui_mode == "demo" or resolved.provider == "fake":
        return demo_session()
    return local_session(resolved)
