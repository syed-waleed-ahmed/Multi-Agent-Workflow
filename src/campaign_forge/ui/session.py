"""The app's per-browser-session state, with no Streamlit dependency.

Three modes:

* **demo** (the default, and the public deployment): every agent is answered by
  the deterministic offline responder, and every output is labeled SIMULATED.
  Runs, brands and notes live in this session's memory only. Nothing is written
  on the server (ADR-008), so a visitor's data ends with their browser session.
* **live** (the public deployment, on request; ADR-037): real model calls on
  Groq, with the owner's key or the visitor's own. Storage stays in session
  memory as in demo mode, settings are isolated from the deployment's
  environment, and every run is capped (:mod:`campaign_forge.services.live`).
* **local** (``CF_UI_MODE=local``): your configured provider, plus the run store
  and memory database the CLI uses (``CF_RUNS_DIR`` or SQLite, ``CF_MEMORY_DB``).

Live and local runs execute in a background thread, so the page stays responsive
while a free tier waits out its rate limits.

The Streamlit pages only read and call this object. Every engine and memory
call goes through it with the session's scope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..config import Settings, isolated_settings
from ..domain.artifacts import Artifact
from ..domain.brand import BrandProfile
from ..domain.scope import Scope
from ..exceptions import ConfigurationError, LiveQuotaError
from ..memory.sqlite import IN_MEMORY, SqliteMemoryStore
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.engine import WorkflowEngine
from ..orchestration.factory import live_engine, simulated_engine
from ..orchestration.state import RunRecord
from ..providers.router import ProviderRouter
from ..services.live import LiveLimits, LiveQuota
from ..services.runs import RunService

UIMode = Literal["demo", "live", "local"]
KeySource = Literal["owner", "visitor"]

#: A Groq key's shape. Requiring it means the key-shaped redaction pattern
#: (``gsk_...``) always matches a visitor's key, wherever it might appear.
GROQ_KEY_PATTERN = re.compile(r"gsk_[A-Za-z0-9]{20,200}")
#: Deployment settings a live session keeps (none of them can reach another key).
_LIVE_PASSTHROUGH = ("model", "temperature", "max_tokens", "request_timeout", "max_retries")

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
    #: Live mode only: the process-wide caps, and whose key the session uses.
    quota: LiveQuota | None = None
    key_source: KeySource | None = None
    #: Owner-key live runs this session has started.
    live_runs: int = 0
    service: RunService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (self.mode == "live") != (self.quota is not None and self.key_source is not None):
            raise ConfigurationError("A live session needs a quota and a key source.")
        # Demo runs finish in a moment; live and local runs may wait out rate limits.
        self.service = RunService(
            self.engine,
            self.memory,
            background=self.mode != "demo",
            history=self.settings.memory_history,
            slots=self.quota.slots if self.quota is not None else None,
            secrets=self.settings.secret_values(),
        )

    @property
    def simulated(self) -> bool:
        return self.mode == "demo"

    @property
    def background(self) -> bool:
        return self.service.background

    @property
    def owner_key(self) -> bool:
        """Live on the deployment's own key (the per-session and daily caps apply)."""
        return self.key_source == "owner"

    def runs_left(self) -> int | None:
        """Owner-key live runs this session may still start (``None``: not capped)."""
        if self.quota is None or not self.owner_key:
            return None
        session_left = self.quota.limits.session_runs - self.live_runs
        return max(min(session_left, self.quota.owner_runs_left_today()), 0)

    def capped(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        """The plan as this session may run it: live runs get the per-run caps."""
        return self.quota.limits.cap(definition) if self.quota is not None else definition

    def start(
        self, definition: WorkflowDefinition, inputs: Mapping[str, BaseModel | Artifact]
    ) -> str:
        """Validate, then start a run. Returns its id (the run may still be executing).

        In live mode the plan is capped here, whatever the page sent.

        Raises:
            WorkflowValidationError: if the definition or inputs are invalid. No run
                is created in that case.
            LiveQuotaError: if an owner-key cap (session or daily) is used up.
            CapacityError: if every live slot is taken.
        """
        plan = self.capped(definition)
        if self.quota is None or not self.owner_key:
            return self.service.start(plan, inputs, scope=self.scope)
        if self.live_runs >= self.quota.limits.session_runs:
            raise LiveQuotaError(
                f"This session has used its {self.quota.limits.session_runs} live runs on the "
                "demo key. Add your own free Groq key to keep going, or return to demo mode."
            )
        self.quota.reserve_owner_run()
        try:
            run_id = self.service.start(plan, inputs, scope=self.scope)
        except BaseException:
            self.quota.refund_owner_run()
            raise
        self.live_runs += 1
        return run_id

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


def visitor_key(raw: str) -> str:
    """A visitor's Groq key, checked for shape only (it is never stored or echoed).

    Raises:
        ConfigurationError: if the text is not shaped like a Groq key.
    """
    key = raw.strip()
    if not GROQ_KEY_PATTERN.fullmatch(key):
        raise ConfigurationError(
            "That does not look like a Groq API key (it starts with gsk_). Nothing was kept."
        )
    return key


def live_settings(key: str, base: Settings | None = None) -> Settings:
    """Isolated settings for a live session: Groq with ``key``, Wikipedia research only.

    Nothing comes from the environment, so no other key, fallback provider or
    routing override of the deployment is reachable. When the deployment itself
    routes to Groq, the model and sampling settings it sets explicitly carry over.
    Only explicit ones: an explicit model pins every class to it, which would turn
    off cap-aware routing (ADR-035).
    """
    values: dict[str, object] = {
        "provider": "groq",
        "groq_api_key": key,
        "research_tools": "wikipedia",
    }
    if base is not None and ProviderRouter(base).global_provider == "groq":
        chosen = base.model_fields_set
        values.update({name: getattr(base, name) for name in _LIVE_PASSTHROUGH if name in chosen})
    return isolated_settings(**values)


def live_session(
    key: str, *, key_source: KeySource, quota: LiveQuota, base: Settings | None = None
) -> AppSession:
    """Real model calls on Groq with ``key``, session-memory storage, capped runs."""
    settings = live_settings(key, base)
    engine = live_engine(settings)
    memory = SqliteMemoryStore(IN_MEMORY, secrets=settings.secret_values())
    session = AppSession(
        mode="live",
        settings=settings,
        engine=engine,
        memory=memory,
        quota=quota,
        key_source=key_source,
    )
    memory.save_brand(session.scope, SAMPLE_BRAND)
    return session


def demo_session() -> AppSession:
    """A simulated session: offline responder, in-memory stores, a sample brand."""
    settings = Settings(_env_file=None, provider="fake", ui_mode="demo")
    engine = simulated_engine()
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


@dataclass
class Deployment:
    """What every browser session of one app process shares: the deployment's settings
    and the live-mode caps (one :class:`LiveQuota`)."""

    settings: Settings
    quota: LiveQuota

    @classmethod
    def from_settings(cls, settings: Settings) -> Deployment:
        return cls(settings, LiveQuota(LiveLimits.from_settings(settings)))

    @property
    def public(self) -> bool:
        """The public app (demo mode by default), where live mode may be offered."""
        return self.settings.ui_mode == "demo" and self.settings.provider != "fake"

    @property
    def owner_key_offered(self) -> bool:
        """Live runs on the owner's key: the owner opted in and a Groq key is configured."""
        return (
            self.public
            and self.settings.live_owner_key
            and self.settings.secret("groq_api_key") is not None
        )

    @property
    def visitor_keys_offered(self) -> bool:
        return self.public and self.settings.live_visitor_keys

    def first_session(self) -> AppSession:
        """The session a new browser session starts with (demo, unless ``CF_UI_MODE=local``)."""
        return build_session(self.settings)

    def owner_session(self) -> AppSession:
        """A live session on the owner's key.

        Raises:
            ConfigurationError: if the deployment does not offer it.
        """
        key = self.settings.secret("groq_api_key")
        if not self.owner_key_offered or key is None:
            raise ConfigurationError("Live runs on the demo's key are not offered here.")
        return live_session(key, key_source="owner", quota=self.quota, base=self.settings)

    def visitor_session(self, raw_key: str) -> AppSession:
        """A live session on a visitor's own Groq key, held in this object only.

        Raises:
            ConfigurationError: if visitor keys are not offered, or the text is not a key.
        """
        if not self.visitor_keys_offered:
            raise ConfigurationError("Live runs with your own key are not offered here.")
        key = visitor_key(raw_key)
        return live_session(key, key_source="visitor", quota=self.quota, base=self.settings)
