"""Session 4: research tools, user sources, evidence-backed agents, fact checking."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from campaign_forge import cli
from campaign_forge.agents.library import (
    AudienceResearchAgent,
    CampaignReporterAgent,
    CompetitorResearchAgent,
    FactCheckerAgent,
    MarketResearchAgent,
)
from campaign_forge.agents.schemas import (
    BRIEF_KIND,
    COMPETITORS_KIND,
    FACT_CHECK_KIND,
    MARKET_KIND,
    CompetitorResearch,
    FactCheck,
)
from campaign_forge.config import Settings
from campaign_forge.core import AgentRegistry, BudgetLimits, EventBus, EventType, RunContext
from campaign_forge.domain import Artifact, Basis, Claim, Evidence, ProducedBy
from campaign_forge.domain.sources import SOURCES_KIND, SourceBundle, SourceDocument
from campaign_forge.evaluation import check_artifact, critical_failures
from campaign_forge.evaluation.offline import OfflineSearch, offline_responder
from campaign_forge.exceptions import (
    BudgetExceededError,
    ConfigurationError,
    ToolError,
    WorkflowValidationError,
)
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import (
    StepDefinition,
    WorkflowDefinition,
    WorkflowEngine,
    default_registry,
    get_template,
    validate_definition,
)
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter
from campaign_forge.tools import (
    TavilySearch,
    ToolBox,
    WikipediaSearch,
    build_toolbox,
    bundle_evidence,
    load_source_file,
    load_source_url,
    load_sources,
)
from campaign_forge.tools.base import EvidenceStore
from campaign_forge.tools.fetch import SafeFetcher

BRIEF = CampaignBrief(
    product_name="EcoSip Bottle",
    product_description="Insulated bottle. Main rival: BrightFlask.",
    target_audience="hikers",
    goal="summer sales",
    tone="fresh",
    channels=["instagram", "email"],
)


def _brief_artifact() -> Artifact:
    return Artifact.from_model(BRIEF_KIND, BRIEF, produced_by=ProducedBy(agent_id="user"))


def _ctx(
    responder: Callable[[CompletionRequest], str] | str = offline_responder,
    *,
    tools: ToolBox | None = None,
    **limits: Any,
) -> tuple[RunContext, FakeProvider]:
    fake = FakeProvider(responder)
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    router = ProviderRouter(settings, providers={"fake": fake})
    ctx = RunContext.create(
        router, limits=BudgetLimits(**limits) if limits else None, events=EventBus(history_size=50)
    )
    ctx.tools = tools
    return ctx, fake


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# User sources
# --------------------------------------------------------------------------- #
def test_load_source_files(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# Notes\nRival: BrightFlask", encoding="utf-8")
    (tmp_path / "data.csv").write_text("name,price\nBrightFlask,30\n", encoding="utf-8")
    (tmp_path / "facts.json").write_text('{"rival": "BrightFlask"}', encoding="utf-8")
    docs = [load_source_file(tmp_path / n) for n in ("notes.md", "data.csv", "facts.json")]
    assert docs[1].content.splitlines() == ["name, price", "BrightFlask, 30"]
    assert '"rival": "BrightFlask"' in docs[2].content
    assert docs[0].origin == "file:notes.md"
    evidence = bundle_evidence(SourceBundle(documents=tuple(docs)))
    assert [e.source for e in evidence] == ["user:notes.md", "user:data.csv", "user:facts.json"]
    assert evidence == bundle_evidence(SourceBundle(documents=tuple(docs)))  # stable ids


def test_bad_source_files_are_configuration_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("  ", encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 50, encoding="utf-8")
    for name, message in (
        ("a.pdf", "unsupported"),
        ("bad.json", "not valid JSON"),
        ("empty.txt", "empty"),
        ("missing.txt", "Cannot read"),
    ):
        with pytest.raises(ConfigurationError, match=message):
            load_source_file(tmp_path / name)
    monkeypatch.setattr("campaign_forge.tools.sources.MAX_FILE_BYTES", 10)
    with pytest.raises(ConfigurationError, match="larger than"):
        load_source_file(tmp_path / "big.txt")


def test_source_urls_use_the_safe_fetcher(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text="<title>Rival page</title><p>BrightFlask sells bottles.</p>",
            headers={"content-type": "text/html"},
        )

    fetcher = SafeFetcher(client=_mock_client(handler), resolver=lambda h, p: ["93.184.216.34"])
    doc = load_source_url("https://rival.test/about", fetcher)
    assert (doc.name, doc.origin) == ("Rival page", "https://rival.test/about")
    with pytest.raises(ConfigurationError, match="non-public"):
        load_source_url("http://127.0.0.1/", SafeFetcher(client=_mock_client(handler)))
    (tmp_path / "n.txt").write_text("notes", encoding="utf-8")
    bundle = load_sources([str(tmp_path / "n.txt"), "https://rival.test/x"], fetcher)
    assert [d.origin for d in bundle.documents] == ["file:n.txt", "https://rival.test/x"]
    assert bundle_evidence(bundle)[1].source == "https://rival.test/x"


# --------------------------------------------------------------------------- #
# Search adapters and toolbox
# --------------------------------------------------------------------------- #
def test_wikipedia_search_parses_results_in_rank_order() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        seen["params"] = dict(request.url.params)
        pages = [
            {
                "index": 2,
                "title": "Second",
                "extract": "Second extract.",
                "fullurl": "https://en.wikipedia.org/wiki/Second",
            },
            {
                "index": 1,
                "title": "First",
                "extract": "First extract.",
                "fullurl": "https://en.wikipedia.org/wiki/First",
            },
            {"index": 3, "title": "Empty", "extract": "", "fullurl": "x"},
        ]
        return httpx.Response(200, json={"query": {"pages": pages}})

    results = WikipediaSearch(client=_mock_client(handler)).search("insulated bottle", limit=9)
    assert [e.title for e in results] == ["First", "Second"]
    assert seen["ua"].startswith("CampaignForge/") and "github.com" in seen["ua"]
    assert seen["params"]["gsrlimit"] == "5" and seen["params"]["gsrsearch"] == "insulated bottle"
    with pytest.raises(ToolError, match="Wikipedia search failed"):
        WikipediaSearch(client=_mock_client(lambda r: httpx.Response(503))).search("x")


def test_tavily_search_and_key_redaction() -> None:
    key = "tvly-" + "k" * 24

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {key}"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://a.test", "title": "A", "content": "Alpha."},
                    {"url": "b", "content": ""},
                ]
            },
        )

    results = TavilySearch(key, client=_mock_client(handler)).search("q")
    assert [(e.source, e.title) for e in results] == [("https://a.test", "A")]
    failing = TavilySearch(
        key, client=_mock_client(lambda r: httpx.Response(401, text=f"bad {key}"))
    )
    with pytest.raises(ToolError) as excinfo:
        failing.search("q")
    assert key not in str(excinfo.value)
    with pytest.raises(ToolError, match="TAVILY_API_KEY"):
        TavilySearch("")


def test_build_toolbox_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    base: dict[str, Any] = {"_env_file": None}
    assert build_toolbox(Settings(**base)).ids == ("wikipedia",)
    assert build_toolbox(Settings(**base, research_tools="")).ids == ()
    with pytest.raises(ConfigurationError, match="TAVILY_API_KEY"):
        build_toolbox(Settings(**base, research_tools="wikipedia,tavily"))
    with pytest.raises(ConfigurationError, match="Unknown research tool"):
        build_toolbox(Settings(**base, research_tools="bing"))
    both = build_toolbox(
        Settings(**base, research_tools="tavily, wikipedia", tavily_api_key="tvly-" + "x" * 20)
    )
    assert both.ids == ("tavily", "wikipedia") and len(both) == 2
    assert [s.id for s in both.specs()] == ["tavily", "wikipedia"]


def test_evidence_store_and_toolbox_allowlist() -> None:
    store = EvidenceStore()
    item = Evidence.create(source="s", excerpt="e")
    store.add([item, item])
    assert len(store) == 1 and store.get(item.id) == item and store.ids() == {item.id}
    box = ToolBox([OfflineSearch()])
    assert [t.spec.id for t in box.allowed(("wikipedia", "offline_search"))] == ["offline_search"]
    assert box.allowed(()) == []


# --------------------------------------------------------------------------- #
# RunContext.search: allowlist, budget, degradation
# --------------------------------------------------------------------------- #
class _Broken:
    spec = OfflineSearch().spec.model_copy(update={"id": "wikipedia"})

    def search(self, query: str, *, limit: int = 3) -> list[Evidence]:
        raise ToolError("down")


def test_search_enforces_allowlist_budget_and_degrades_gracefully() -> None:
    ctx, _ = _ctx(tools=ToolBox([OfflineSearch(), _Broken()]), max_tool_calls=3)
    spec = MarketResearchAgent.spec
    assert set(ctx.search_tools(spec)) == {"offline_search", "wikipedia"}
    found = ctx.search(spec, "bottles")
    assert len(found) == 2 and len(ctx.evidence) == 2
    kinds = [e.type for e in ctx.events.history]
    assert EventType.TOOL_CALL_FINISHED in kinds and EventType.WARNING in kinds
    assert ctx.budget.snapshot().tool_calls == 2
    with pytest.raises(BudgetExceededError, match="tool calls"):
        ctx.search(spec, "more")
    no_tools = AudienceResearchAgent.spec.model_copy(update={"allowed_tools": ()})
    assert ctx.search_tools(no_tools) == [] and _ctx()[0].search_tools(spec) == []


# --------------------------------------------------------------------------- #
# Evidence-backed agents
# --------------------------------------------------------------------------- #
def test_market_research_cites_retrieved_evidence() -> None:
    ctx, fake = _ctx(tools=ToolBox([OfflineSearch()]))
    output = MarketResearchAgent().run({"brief": _brief_artifact()}, ctx)
    artifact = output.artifact
    external = [c for c in artifact.claims if c.basis is Basis.EXTERNAL_SOURCE]
    assert external and {e.id for e in artifact.evidence} == {
        i for c in external for i in c.evidence_ids
    }
    assert fake.requests[0].metadata["phase"] == "plan"
    assert "[ev-" in fake.requests[1].messages[1].content
    assert any(n.startswith("evidence mode") for n in output.notes)
    assert not critical_failures(check_artifact(artifact, BRIEF))


def test_invented_citations_are_repaired() -> None:
    calls = {"n": 0}

    def responder(request: CompletionRequest) -> str:
        if request.metadata.get("phase") == "plan":
            return offline_responder(request)
        calls["n"] += 1
        data = json.loads(offline_responder(request))
        if calls["n"] == 1:
            data["trends"][0]["evidence_ids"] = ["ev-ffffffffffff"]
        return json.dumps(data)

    ctx, fake = _ctx(responder, tools=ToolBox([OfflineSearch()]))
    output = MarketResearchAgent().run({"brief": _brief_artifact()}, ctx)
    assert "unknown evidence ids ['ev-ffffffffffff']" in fake.requests[2].messages[-1].content
    assert "ev-ffffffffffff" not in json.dumps(output.artifact.data)


def test_assumptions_mode_without_tools_or_sources() -> None:
    ctx, fake = _ctx()
    output = AudienceResearchAgent().run({"brief": _brief_artifact()}, ctx)
    assert len(fake.requests) == 1  # no plan call without tools
    assert "No research evidence is available" in fake.requests[0].messages[1].content
    assert all(c.basis is not Basis.EXTERNAL_SOURCE for c in output.artifact.claims)
    assert output.artifact.evidence == ()
    assert output.notes[0].startswith("assumptions mode")


def test_user_sources_alone_enable_evidence_mode() -> None:
    bundle = SourceBundle(
        documents=(SourceDocument(name="notes.md", content="Hikers want light bottles."),)
    )
    sources = Artifact.from_model(SOURCES_KIND, bundle, produced_by=ProducedBy(agent_id="user"))
    ctx, fake = _ctx()
    output = AudienceResearchAgent().run({"brief": _brief_artifact(), "sources": sources}, ctx)
    assert (
        len(fake.requests) == 1 and "(user source) notes.md" in fake.requests[0].messages[1].content
    )
    assert [e.source for e in output.artifact.evidence] == ["user:notes.md"]


def test_competitors_must_be_named_in_reference_material() -> None:
    calls = {"n": 0}

    def responder(request: CompletionRequest) -> str:
        if request.metadata.get("phase") == "plan":
            return offline_responder(request)
        calls["n"] += 1
        data = json.loads(offline_responder(request))
        if calls["n"] == 1:
            data["competitors"].append({**data["competitors"][0], "name": "MadeUpCorp"})
        return json.dumps(data)

    ctx, fake = _ctx(responder, tools=ToolBox([OfflineSearch()]))
    output = CompetitorResearchAgent().run({"brief": _brief_artifact()}, ctx)
    names = [c["name"] for c in output.artifact.data["competitors"]]
    assert names == ["RivalCo"]
    assert "['MadeUpCorp'] are not named" in fake.requests[2].messages[-1].content
    research = output.artifact.as_model(CompetitorResearch)
    assert "| RivalCo |" in research.to_markdown() and "RivalCo" in research.digest()
    empty = CompetitorResearch.model_validate({"opportunities": [{"statement": "gap"}]})
    assert "No named competitors" in empty.to_markdown() and "No named" in empty.digest()


# --------------------------------------------------------------------------- #
# Fact checker
# --------------------------------------------------------------------------- #
def _research_with_claims(*claims: Claim, evidence: tuple[Evidence, ...] = ()) -> Artifact:
    data = json.loads(
        offline_responder(
            CompletionRequest.from_prompts(
                "s", BRIEF.as_prompt_context(), metadata={"agent": "market_research"}
            )
        )
    )
    return Artifact(
        kind=MARKET_KIND,
        data=data,
        claims=claims,
        evidence=evidence,
        produced_by=ProducedBy(agent_id="m"),
    )


def test_fact_checker_without_external_claims_makes_no_call() -> None:
    ctx, fake = _ctx()
    claim = Claim(statement="Assumed.", basis=Basis.MODEL_ASSUMPTION)
    output = FactCheckerAgent().run({"market": _research_with_claims(claim)}, ctx)
    assert fake.call_count == 0
    assert output.artifact.as_model(FactCheck).claims == []
    assert "nothing to check" in output.notes[0]


def test_fact_checker_maps_verdicts_by_index() -> None:
    ev = Evidence.create(source="https://a.test", excerpt="Bottles keep drinks cold.")
    claims = (
        Claim(statement="Keeps drinks cold.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=(ev.id,)),
        Claim(
            statement="Cheapest on the market.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=(ev.id,)
        ),
    )
    answers = iter(
        [
            json.dumps({"verdicts": [{"index": 5, "verdict": "supported"}]}),
            json.dumps({"verdicts": [{"index": 0, "verdict": "supported"}]}),
        ]
    )
    ctx, fake = _ctx(lambda r: next(answers))
    output = FactCheckerAgent().run({"market": _research_with_claims(*claims, evidence=(ev,))}, ctx)
    result = output.artifact.as_model(FactCheck)
    assert [c.verdict for c in result.claims] == ["supported", "not_checked"]
    assert (result.supported, result.not_checked) == (1, 1)
    assert "out of range: [5]" in fake.requests[1].messages[-1].content
    assert output.artifact.evidence == (ev,)
    assert "| Claim |" in result.to_markdown()
    advisory = check_artifact(
        Artifact(
            kind=FACT_CHECK_KIND,
            data={"unsupported": 2, "claims": []},
            produced_by=ProducedBy(agent_id="f"),
        )
    )
    assert any(r.name == "claims_supported" and not r.passed and not r.critical for r in advisory)


def test_fabricated_citations_are_critical_check_failures() -> None:
    claim = Claim(
        statement="Up 20%.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=("ev-000000000000",)
    )
    results = check_artifact(_research_with_claims(claim), BRIEF)
    assert "citations_verified" in [r.name for r in critical_failures(results)]


def test_reporter_lists_sources() -> None:
    ev = Evidence.create(source="https://a.test", title="A study", excerpt="x")
    claim = Claim(statement="Cited.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=(ev.id,))
    from tests.test_library_agents import _inputs_for  # reuse the upstream-input builder

    inputs = _inputs_for(CampaignReporterAgent)
    inputs["market"] = _research_with_claims(claim, evidence=(ev,))
    ctx, _ = _ctx()
    markdown = CampaignReporterAgent().run(inputs, ctx).artifact.data["markdown"]
    assert "## Sources" in markdown and f"[{ev.id}] A study - https://a.test" in markdown


# --------------------------------------------------------------------------- #
# Templates, optional inputs, CLI
# --------------------------------------------------------------------------- #
def _engine(tools: ToolBox | None = None) -> WorkflowEngine:
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    router = ProviderRouter(settings, providers={"fake": FakeProvider(offline_responder)})
    return WorkflowEngine(default_registry(), router, tools=tools)


def test_competitor_report_with_and_without_sources() -> None:
    engine = _engine(ToolBox([OfflineSearch()]))
    result = engine.start(get_template("competitor-report"), {"brief": BRIEF})
    assert result.ok
    check = result.output("fact_check", FactCheck)
    assert check.claims and check.unsupported == 0
    bundle = SourceBundle(documents=(SourceDocument(name="n", content="BrightFlask is a rival."),))
    with_sources = _engine().start(
        get_template("competitor-report"), {"brief": BRIEF, "sources": bundle}
    )
    assert with_sources.ok
    assert with_sources.record.steps["competitors"].notes


def test_optional_input_rules() -> None:
    registry = default_registry()
    with pytest.raises(ValueError, match="both required and optional"):
        WorkflowDefinition(
            id="x",
            name="x",
            inputs={"brief": BRIEF_KIND},
            optional_inputs={"brief": BRIEF_KIND},
            steps=(
                StepDefinition(id="a", agent="audience_research", inputs={"brief": "input:brief"}),
            ),
        )
    bad = WorkflowDefinition(
        id="x",
        name="x",
        inputs={},
        optional_inputs={"brief": BRIEF_KIND},
        steps=(StepDefinition(id="a", agent="audience_research", inputs={"brief": "input:brief"}),),
    )
    with pytest.raises(WorkflowValidationError, match="bound to optional workflow input"):
        validate_definition(bad, registry)
    assert get_template("full-campaign").input_kind("sources") == SOURCES_KIND
    assert get_template("full-campaign").input_kind("nope") is None


def test_cli_sources_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CF_PROVIDER", "fake")
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    from campaign_forge.config import get_settings

    get_settings.cache_clear()
    brief = tmp_path / "brief.json"
    brief.write_text(BRIEF.model_dump_json(), encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("BrightFlask is our main rival.", encoding="utf-8")
    try:
        ok = cli.main(
            [
                "execute",
                "competitor-report",
                "--brief",
                str(brief),
                "--sources",
                str(notes),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        refused = cli.main(
            ["execute", "classic-campaign", "--brief", str(brief), "--sources", str(notes)]
        )
    finally:
        get_settings.cache_clear()
    assert ok == 0 and payload["status"] == "completed"
    assert refused == 2
    assert "does not accept --sources" in capsys.readouterr().out
    assert COMPETITORS_KIND and AgentRegistry  # imported for completeness
