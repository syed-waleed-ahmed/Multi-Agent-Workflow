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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..config import Settings
from ..domain.artifacts import Artifact
from ..domain.brand import BrandProfile
from ..domain.scope import Scope
from ..evaluation.offline import OfflineSearch, offline_responder
from ..memory.sqlite import IN_MEMORY, SqliteMemoryStore
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.engine import WorkflowEngine
from ..orchestration.state import RunRecord
from ..orchestration.store import InMemoryRunStore
from ..orchestration.templates import default_registry
from ..providers.fake import FakeProvider
from ..providers.router import ProviderRouter
from ..services.runs import RunService
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
    """Everything one browser session can see and do. Run control is a RunService."""

    mode: UIMode
    settings: Settings
    engine: WorkflowEngine
    memory: SqliteMemoryStore
    scope: Scope = field(default_factory=Scope.local)
    service: RunService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Demo runs finish in a moment; local runs may wait out rate limits for minutes.
        self.service = RunService(
            self.engine,
            self.memory,
            background=self.mode == "local",
            history=self.settings.memory_history,
        )

    @property
    def simulated(self) -> bool:
        return self.mode == "demo"

    @property
    def background(self) -> bool:
        return self.service.background

    def start(
        self, definition: WorkflowDefinition, inputs: Mapping[str, BaseModel | Artifact]
    ) -> str:
        """Validate, then start a run. Returns its id (the run may still be executing).

        Raises:
            WorkflowValidationError: if the definition or inputs are invalid. No run
                is created in that case.
        """
        return self.service.start(definition, inputs, scope=self.scope)

    def resume(self, run_id: str) -> None:
        self.service.resume(run_id, scope=self.scope)

    def approve(self, run_id: str, step_id: str, *, approved: bool, comment: str = "") -> None:
        self.service.approve(run_id, step_id, scope=self.scope, approved=approved, comment=comment)

    def cancel(self, run_id: str) -> RunRecord:
        return self.service.cancel(run_id, scope=self.scope)

    def record(self, run_id: str) -> RunRecord | None:
        """The run's latest checkpoint, or ``None`` before its first save."""
        return self.service.record(run_id, scope=self.scope)

    def runs(self) -> list[RunRecord]:
        return self.service.runs(scope=self.scope)

    def is_running(self, run_id: str) -> bool:
        return self.service.is_running(run_id, scope=self.scope)

    def error(self, run_id: str) -> str | None:
        """An error that stopped a background run before or outside the engine's record."""
        return self.service.error(run_id, scope=self.scope)

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
