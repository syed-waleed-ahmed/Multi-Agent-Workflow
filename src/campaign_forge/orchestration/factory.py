"""The engine every interface builds from settings (CLI, web app in local mode, API).

With ``CF_PROVIDER=fake`` every agent is answered by the deterministic offline
responder and research uses the simulated search: a key-free, network-free
demonstration whose output is labeled simulated wherever it is shown.
"""

from __future__ import annotations

from ..config import Settings
from ..evaluation.offline import OfflineSearch, offline_responder
from ..providers.fake import FakeProvider
from ..providers.router import ProviderRouter
from ..tools import ToolBox, build_toolbox
from .engine import WorkflowEngine
from .store import InMemoryRunStore, JsonFileRunStore, RunStore, SqliteRunStore
from .templates import default_registry


def build_engine(settings: Settings) -> WorkflowEngine:
    """Built-in plus plugin agents, the configured provider, and the configured run store."""
    router = ProviderRouter(settings)
    if router.global_provider == "fake":
        router.add_provider("fake", FakeProvider(offline_responder))
        tools = ToolBox([OfflineSearch()])
    else:
        tools = build_toolbox(settings)
    store: RunStore = (
        SqliteRunStore(settings.memory_db)
        if settings.run_store == "sqlite"
        else JsonFileRunStore(settings.runs_dir)
    )
    return WorkflowEngine(default_registry(load_plugins=True), router, store=store, tools=tools)


def live_engine(settings: Settings) -> WorkflowEngine:
    """The public demo's live engine (ADR-037): the settings' provider and research tools,
    built-in agents only (no plugins), and runs kept in memory (nothing on the server)."""
    return WorkflowEngine(
        default_registry(),
        ProviderRouter(settings),
        store=InMemoryRunStore(),
        tools=build_toolbox(settings),
    )


def simulated_engine() -> WorkflowEngine:
    """A key-free, network-free engine: the offline responder, simulated search, runs kept
    in memory (the web app's demo mode and offline benchmarks)."""
    settings = Settings(_env_file=None, provider="fake")
    router = ProviderRouter(settings)
    router.add_provider("fake", FakeProvider(offline_responder))
    return WorkflowEngine(
        default_registry(), router, store=InMemoryRunStore(), tools=ToolBox([OfflineSearch()])
    )
