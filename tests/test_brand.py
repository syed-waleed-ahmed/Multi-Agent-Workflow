"""Session 5 brand features: profiles, term checks, brand agents, templates, history and CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from campaign_forge import cli
from campaign_forge.agents.library import (
    BrandConsistencyAgent,
    BrandStrategistAgent,
    CopywriterAgent,
    CriticAgent,
)
from campaign_forge.agents.library.brand import content_text
from campaign_forge.agents.schemas import (
    ADS_KIND,
    BRAND_CHECK_KIND,
    BRIEF_KIND,
    CORE_COPY_KIND,
    POSITIONING_KIND,
    BrandCheck,
    CampaignReport,
    CoreCopy,
)
from campaign_forge.cli_runs import build_engine
from campaign_forge.config import Settings
from campaign_forge.core import RunContext
from campaign_forge.domain import (
    BRAND_KIND,
    REVIEW_SUBJECT_INPUT,
    Artifact,
    BrandProfile,
    ProducedBy,
    Scope,
    brand_slug,
    term_counts,
)
from campaign_forge.evaluation.offline import RESPONDERS, offline_responder
from campaign_forge.exceptions import WorkflowValidationError
from campaign_forge.memory import SqliteMemoryStore
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import (
    RunStatus,
    SqliteRunStore,
    WorkflowEngine,
    default_registry,
    get_template,
    validate_definition,
)
from campaign_forge.orchestration.history import history_document, remember_run
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter

BRIEF = CampaignBrief(
    product_name="EcoSip Bottle",
    product_description="Insulated bottle that keeps drinks cold for 24 hours.",
    target_audience="hikers",
    goal="summer sales",
    tone="fresh",
    channels=["instagram", "google ads", "email"],
)
BRAND = BrandProfile(
    name="EcoSip",
    voice="Plain, warm and practical.",
    tone_words=["warm", "plain"],
    preferred_terms=["for years", "every day", "trail-ready"],
    banned_terms=["cheap", "game changing"],
)
CLEAN_COPY = {
    "tagline": "EcoSip: made to last",
    "primary_message": "EcoSip keeps up with your day, every day, for years.",
    "headlines": ["Meet EcoSip", "Built for every day", "Buy once"],
    "body_copy": "EcoSip is designed for people who want things that simply work.",
    "call_to_action": "Get EcoSip today",
}


def _artifact(kind: str, value: Any) -> Artifact:
    if isinstance(value, dict):
        return Artifact(kind=kind, data=value, produced_by=ProducedBy(agent_id="test"))
    return Artifact.from_model(kind, value, produced_by=ProducedBy(agent_id="test"))


def _router(responder: Any) -> tuple[ProviderRouter, FakeProvider]:
    fake = FakeProvider(responder)
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    return ProviderRouter(settings, providers={"fake": fake}), fake


def _ctx(responder: Any) -> tuple[RunContext, FakeProvider]:
    router, fake = _router(responder)
    return RunContext.create(router), fake


def _engine(responder: Any = offline_responder) -> WorkflowEngine:
    return WorkflowEngine(default_registry(), _router(responder)[0])


# --------------------------------------------------------------------------- #
# Domain
# --------------------------------------------------------------------------- #
def test_brand_slug_and_term_counts() -> None:
    assert brand_slug("  EcoSip Bottle! ") == "ecosip-bottle"
    assert brand_slug("!!!").startswith("brand-")
    text = "Not cheap, never CHEAP. Cheaper? A game   changing idea."
    assert term_counts(text, ["cheap", "game changing", "idea", " "]) == {
        "cheap": 2,  # whole words only: "Cheaper" does not count
        "game changing": 1,  # any whitespace between the words
        "idea": 1,
    }


def test_brand_profile_rules_and_rendering() -> None:
    profile = BrandProfile(
        name="EcoSip",
        voice="Warm.",
        tone_words=["warm", "Warm", "plain"],
        banned_terms=["cheap"],
        visual_style="Natural light.",
    )
    assert profile.tone_words == ["warm", "plain"]  # de-duplicated, case-insensitively
    assert profile.slug == "ecosip"
    assert "Banned terms: cheap" in profile.digest()
    assert "**Visual style:** Natural light." in profile.to_markdown()
    assert "(none)" in profile.to_markdown()  # empty sections are explicit
    with pytest.raises(ValidationError, match="both preferred and banned"):
        BrandProfile(name="X", voice="Y", preferred_terms=["Cheap"], banned_terms=["cheap"])


def test_core_copy_rejects_banned_terms_only_with_a_brand_in_play() -> None:
    bad = {**CLEAN_COPY, "body_copy": "A cheap bottle, and a game changing one."}
    assert CoreCopy.model_validate(bad).text().count("\n") == 6
    with pytest.raises(ValidationError, match="'cheap', 'game changing'"):
        CoreCopy.model_validate(bad, context={"banned_terms": BRAND.banned_terms})
    CoreCopy.model_validate(CLEAN_COPY, context={"banned_terms": BRAND.banned_terms})


def test_brand_check_result_is_computed_by_code() -> None:
    clean = BrandCheck(brand="EcoSip", tone_score=0.75, preferred_used=["for years"])
    assert clean.passed and "passed" in clean.digest()
    off_tone = BrandCheck(brand="EcoSip", tone_score=0.25)
    assert not off_tone.passed
    hits = BrandCheck(
        brand="EcoSip",
        banned_hits=[{"term": "cheap", "deliverable": "ads", "count": 2}],  # type: ignore[list-item]
        preferred_missing=["for years"],
        tone_notes=["Too salesy."],
    )
    assert not hits.passed
    markdown = hits.to_markdown()
    assert "| cheap | ads | 2 |" in markdown
    assert "for years" in markdown and "Too salesy" in markdown
    assert "not judged" in hits.digest()


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
def test_brand_consistency_counts_terms_by_code_and_frames_everything() -> None:
    copy = {**CLEAN_COPY, "body_copy": "Cheap? Never. Not cheap at all."}
    variant = {
        "platform": "meta",
        "headline": "A game changing bottle",
        "primary_text": "Trail-ready, for years.",
        "description": "",
        "call_to_action": "Shop",
        "warnings": ["cheap"],  # written by code: never counted
    }
    ads = {"variants": [variant, {**variant, "headline": "Built to last"}]}
    inputs = {
        "brand": _artifact(BRAND_KIND, BRAND),
        "copy": _artifact(CORE_COPY_KIND, copy),
        "ads": _artifact(ADS_KIND, ads),
    }
    ctx, fake = _ctx(offline_responder)
    output = BrandConsistencyAgent().run(inputs, ctx)
    check = output.artifact.as_model(BrandCheck)
    assert output.artifact.kind == BRAND_CHECK_KIND
    assert [(h.term, h.deliverable, h.count) for h in check.banned_hits] == [
        ("cheap", "copy", 2),
        ("game changing", "ads", 1),
    ]
    assert check.preferred_used == ["for years", "every day", "trail-ready"]
    assert check.tone_score == 0.75 and check.passed is False
    assert check.checked == ["copy", "ads"]
    user = fake.requests[0].messages[1].content
    assert "BEGIN UNTRUSTED DATA: brand profile" in user
    assert "BEGIN UNTRUSTED DATA: work under review" in user
    assert content_text({"weeks": 2, "posts": [{"day": 1, "caption": "hi"}]}) == "hi"
    assert content_text({"platform": "cheap", "headline": "ok"}) == "ok"


def test_copywriter_writes_in_the_brand_voice_and_repairs_banned_terms() -> None:
    answers = [json.dumps({**CLEAN_COPY, "tagline": "The cheap choice"}), json.dumps(CLEAN_COPY)]
    ctx, fake = _ctx(lambda request: answers.pop(0))
    positioning = json.loads(RESPONDERS["positioning"](CompletionRequest.from_prompts("s", "u")))
    inputs = {
        "brief": _artifact(BRIEF_KIND, BRIEF),
        "positioning": _artifact(POSITIONING_KIND, positioning),
        "brand": _artifact(BRAND_KIND, BRAND),
    }
    output = CopywriterAgent().run(inputs, ctx)
    assert output.artifact.as_model(CoreCopy).tagline == "EcoSip: made to last"
    assert any("repaired 1 time" in note for note in output.notes)
    first, second = (r.messages[-1].content for r in fake.requests[:2])
    assert "BEGIN UNTRUSTED DATA: brand profile" in fake.requests[0].messages[1].content
    assert "never use its banned terms" in fake.requests[0].messages[1].content
    assert "'cheap'" in second and first != second

    # Without a brand, the same banned word is fine and no brand text is sent.
    ctx, fake = _ctx(lambda request: json.dumps({**CLEAN_COPY, "tagline": "The cheap choice"}))
    plain = {k: v for k, v in inputs.items() if k != "brand"}
    assert CopywriterAgent().run(plain, ctx).artifact.as_model(CoreCopy).tagline.endswith("choice")
    assert "brand profile" not in fake.requests[0].messages[1].content


def test_critic_reviews_against_the_brand_when_one_is_bound() -> None:
    ctx, fake = _ctx(offline_responder)
    CriticAgent().run(
        {
            "brief": _artifact(BRIEF_KIND, BRIEF),
            "brand": _artifact(BRAND_KIND, BRAND),
            REVIEW_SUBJECT_INPUT: _artifact(CORE_COPY_KIND, CLEAN_COPY),
        },
        ctx,
    )
    assert "BEGIN UNTRUSTED DATA: brand profile" in fake.requests[0].messages[1].content


def test_brand_strategist_notes_its_vocabulary() -> None:
    ctx, _ = _ctx(offline_responder)
    output = BrandStrategistAgent().run({"brief": _artifact(BRIEF_KIND, BRIEF)}, ctx)
    profile = output.artifact.as_model(BrandProfile)
    assert profile.name == "EcoSip Bottle"
    assert output.notes == ("2 preferred and 3 banned term(s)",)


# --------------------------------------------------------------------------- #
# Templates and the guarded optional input
# --------------------------------------------------------------------------- #
def test_brand_kit_produces_a_profile_on_brand_copy_and_a_passing_check() -> None:
    result = _engine().start(get_template("brand-kit"), {"brief": BRIEF})
    assert result.ok, result.record.status_reason
    outputs = result.record.outputs()
    assert set(outputs) == {"brand", "copy", "brand_check"}
    assert outputs["brand_check"].as_model(BrandCheck).passed
    assert outputs["brand"].as_model(BrandProfile).banned_terms


def test_full_campaign_checks_the_brand_only_when_one_is_given() -> None:
    engine = _engine()
    with_brand = engine.start(get_template("full-campaign"), {"brief": BRIEF, "brand": BRAND})
    assert with_brand.ok
    assert with_brand.record.steps["brand_check"].status.value == "succeeded"
    report = with_brand.record.outputs()["report"].as_model(CampaignReport).markdown
    assert "## Brand profile" in report and "## Brand check" in report

    without = engine.start(get_template("full-campaign"), {"brief": BRIEF})
    state = without.record.steps["brand_check"]
    assert without.ok and state.skip_reason is not None
    assert state.skip_reason.value == "condition" and state.detail == "condition not met"
    plain_report = without.record.outputs()["report"].as_model(CampaignReport)
    assert "Brand profile" not in plain_report.missing_inputs  # optional extras are not "missing"


def test_a_required_input_may_bind_an_optional_one_only_behind_exists() -> None:
    definition = get_template("full-campaign")
    registry = default_registry()
    validate_definition(definition, registry)
    steps = tuple(
        s.model_copy(update={"when": ()}) if s.id == "brand_check" else s for s in definition.steps
    )
    unguarded = definition.model_copy(update={"steps": steps})
    with pytest.raises(WorkflowValidationError, match="without an 'exists' condition"):
        validate_definition(unguarded, registry)


# --------------------------------------------------------------------------- #
# Campaign history
# --------------------------------------------------------------------------- #
def test_completed_runs_are_remembered_and_searchable() -> None:
    result = _engine().start(get_template("brand-kit"), {"brief": BRIEF})
    title, text, metadata = history_document(result.record)
    assert title == "EcoSip Bottle - Brand kit"
    assert "Brief:" in text and "Brand Check:" in text
    assert metadata["workflow"] == "brand-kit" and metadata["status"] == "completed"
    with SqliteMemoryStore() as memory:
        document = remember_run(memory, result.record)
        assert document is not None and document.ref == result.record.run_id
        again = remember_run(memory, result.record)
        assert again is not None
        assert [d.id for d in memory.list_documents(Scope.local())] == [again.id]  # replaced
        assert memory.search(Scope.local(), "EcoSip")[0].document.ref == result.record.run_id
        failed = result.record.model_copy(update={"status": RunStatus.FAILED})
        assert remember_run(memory, failed) is None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@pytest.fixture
def offline_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("CF_PROVIDER", "fake")
    monkeypatch.setenv("CF_RUNS_DIR", str(tmp_path / "runs"))
    brief = tmp_path / "brief.json"
    brief.write_text(BRIEF.model_dump_json(), encoding="utf-8")
    brand = tmp_path / "brand.json"
    brand.write_text(BRAND.model_dump_json(), encoding="utf-8")
    return {"brief": brief, "brand": brand, "root": tmp_path}


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out


@pytest.mark.usefixtures("offline_cli")
def test_cli_brand_lifecycle(offline_cli: dict[str, Path], capsys: Any) -> None:
    assert cli.main(["memory", "brand", "save", str(offline_cli["brand"])]) == 0
    assert "Saved brand" in _out(capsys)
    assert cli.main(["memory", "brand", "list", "--json"]) == 0
    assert [b["name"] for b in json.loads(_out(capsys))] == ["EcoSip"]
    assert cli.main(["memory", "brand", "list"]) == 0
    assert "ecosip" in _out(capsys)
    assert cli.main(["memory", "brand", "show", "ecosip"]) == 0
    assert "Banned terms" in _out(capsys)
    assert cli.main(["memory", "brand", "show", "EcoSip", "--json"]) == 0
    assert json.loads(_out(capsys))["banned_terms"] == ["cheap", "game changing"]
    assert cli.main(["memory", "brand", "delete", "EcoSip"]) == 0
    assert cli.main(["memory", "brand", "show", "EcoSip"]) == 1
    assert "not found" in _out(capsys)
    bad = offline_cli["root"] / "bad.json"
    bad.write_text('{"name": "X"}', encoding="utf-8")
    assert cli.main(["memory", "brand", "save", str(bad)]) == 2
    assert "voice" in _out(capsys)
    assert cli.main(["memory", "brand", "save", str(offline_cli["root"] / "missing.json")]) == 2


def test_cli_execute_with_brand_history_and_search(
    offline_cli: dict[str, Path], capsys: Any
) -> None:
    brief = str(offline_cli["brief"])
    assert cli.main(["execute", "brand-kit", "--brief", brief, "--json"]) == 0
    run_id = json.loads(_out(capsys))["run_id"]
    assert cli.main(["memory", "brand", "save", "--run", run_id]) == 0
    assert "EcoSip Bottle" in _out(capsys)

    code = cli.main(["execute", "full-campaign", "--brief", brief, "--brand", "EcoSip Bottle"])
    out = _out(capsys)
    assert code == 0 and "Remembered in campaign history" in out
    brand_file = str(offline_cli["brand"])
    assert cli.main(["execute", "ad-pack", "--brief", brief, "--brand", brand_file]) == 0
    _out(capsys)

    assert cli.main(["memory", "search", "EcoSip", "--kind", "campaign", "--json"]) == 0
    hits = json.loads(_out(capsys))
    assert len(hits) == 3 and {h["document"]["metadata"]["workflow"] for h in hits} == {
        "brand-kit",
        "full-campaign",
        "ad-pack",
    }
    assert cli.main(["memory", "search", "EcoSip"]) == 0
    assert "Memory search" in _out(capsys)
    assert cli.main(["memory", "search", "zzzz"]) == 0
    assert "No matches" in _out(capsys)

    # Refusals: a workflow without a brand input, an unknown brand, a run without a brand.
    assert cli.main(["execute", "classic-campaign", "--brief", brief, "--brand", "EcoSip"]) == 2
    assert "does not accept --brand" in _out(capsys)
    assert cli.main(["execute", "ad-pack", "--brief", brief, "--brand", "Nope"]) == 2
    assert "No brand named 'Nope'" in _out(capsys)
    assert cli.main(["execute", "classic-campaign", "--brief", brief, "--json"]) == 0
    plain_run = json.loads(_out(capsys))["run_id"]
    assert cli.main(["memory", "brand", "save", "--run", plain_run]) == 2
    assert "no brand profile output" in _out(capsys)


def test_cli_notes_listing_and_forget(offline_cli: dict[str, Path], capsys: Any) -> None:
    assert cli.main(["memory", "note", "Launch in June with the hiking clubs"]) == 0
    assert "Stored note" in _out(capsys)
    assert cli.main(["memory", "note", "Use sk-" + "z" * 24, "--title", "Key", "--json"]) == 0
    assert "[REDACTED]" in json.loads(_out(capsys))["text"]
    assert cli.main(["memory", "brand", "save", str(offline_cli["brand"])]) == 0
    assert cli.main(["execute", "classic-campaign", "--brief", str(offline_cli["brief"])]) == 0
    _out(capsys)
    assert cli.main(["memory", "list", "--json"]) == 0
    assert [d["kind"] for d in json.loads(_out(capsys))] == ["campaign", "note", "note"]
    assert cli.main(["memory", "list", "--kind", "note"]) == 0
    assert "Launch in June" in _out(capsys)

    assert cli.main(["memory", "forget", "--runs"]) == 1  # refuses without --yes
    assert "Nothing was deleted" in _out(capsys)
    assert cli.main(["memory", "list", "--json"]) == 0
    assert len(json.loads(_out(capsys))) == 3
    assert cli.main(["memory", "forget", "--yes", "--runs", "--json"]) == 0
    assert json.loads(_out(capsys)) == {"memory_records": 4, "runs": 1}
    assert cli.main(["memory", "list", "--json"]) == 0
    assert json.loads(_out(capsys)) == []
    assert cli.main(["runs", "list", "--json"]) == 0
    assert json.loads(_out(capsys)) == []
    assert cli.main(["memory", "forget", "--yes"]) == 0
    assert "Deleted" in _out(capsys)


def test_history_can_be_turned_off_and_runs_can_live_in_sqlite(
    offline_cli: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("CF_MEMORY_HISTORY", "false")
    monkeypatch.setenv("CF_RUN_STORE", "sqlite")
    assert cli.main(["execute", "classic-campaign", "--brief", str(offline_cli["brief"])]) == 0
    assert "Remembered" not in _out(capsys)
    assert cli.main(["memory", "list", "--json"]) == 0
    assert json.loads(_out(capsys)) == []
    assert cli.main(["runs", "list", "--json"]) == 0
    assert len(json.loads(_out(capsys))) == 1
    assert not (offline_cli["root"] / "runs").exists()  # nothing written as JSON files

    settings = Settings(_env_file=None, provider="fake", run_store="sqlite")  # type: ignore[call-arg]
    assert isinstance(build_engine(settings).store, SqliteRunStore)
