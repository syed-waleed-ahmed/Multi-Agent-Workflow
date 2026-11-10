"""Integration: wave-1 templates end to end, critic-driven revision, deterministic checks."""

from __future__ import annotations

import json
from typing import Any

import pytest

from campaign_forge.agents.schemas import (
    ADS_KIND,
    AUDIENCE_KIND,
    BRIEF_KIND,
    CORE_COPY_KIND,
    EMAIL_KIND,
    REPORT_KIND,
    CampaignReport,
    CoreCopy,
)
from campaign_forge.config import Settings
from campaign_forge.domain import Artifact, Basis, Claim, ProducedBy, ReviewResult
from campaign_forge.evaluation import (
    INJECTION_CANARIES,
    check_artifact,
    check_score,
    critical_failures,
    grounded,
    missing_sections,
    rubric_for,
    text_of,
)
from campaign_forge.evaluation.checks import dumps_compact
from campaign_forge.evaluation.offline import offline_responder
from campaign_forge.exceptions import WorkflowValidationError
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import (
    BUILTIN_TEMPLATES,
    ReviewSpec,
    SkipReason,
    StepDefinition,
    WorkflowDefinition,
    WorkflowEngine,
    default_registry,
    get_template,
    validate_definition,
)
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter

BRIEF = CampaignBrief(
    product_name="EcoSip Bottle",
    product_description="Insulated bottle that keeps drinks cold for 24 hours.",
    target_audience="hikers",
    goal="summer sales",
    tone="fresh",
    channels=["instagram", "google ads", "email"],
)


def _engine(responder: Any = offline_responder) -> tuple[WorkflowEngine, FakeProvider]:
    fake = FakeProvider(responder)
    router = ProviderRouter(Settings(_env_file=None, provider="fake"), providers={"fake": fake})  # type: ignore[call-arg]
    return WorkflowEngine(default_registry(), router), fake


@pytest.mark.parametrize("template_id", sorted(BUILTIN_TEMPLATES))
def test_every_template_is_valid_and_completes_offline(template_id: str) -> None:
    engine, _ = _engine()
    definition = get_template(template_id)
    validate_definition(definition, engine.registry)
    result = engine.start(definition, {"brief": BRIEF})
    assert result.ok, result.record.status_reason
    for artifact in result.outputs().values():
        assert not critical_failures(check_artifact(artifact, BRIEF)), artifact.kind


def test_full_campaign_outputs_provenance_and_usage() -> None:
    engine, fake = _engine()
    result = engine.start(get_template("full-campaign"), {"brief": BRIEF})
    record = result.record
    assert result.ok and record.llm_calls == 12  # 10 agents + 2 critic reviews
    report = result.output("report", CampaignReport)
    assert report.missing_inputs == []
    assert len(report.assumptions) == 3
    audience = next(a for a in record.artifacts.values() if a.kind == AUDIENCE_KIND)
    assert {c.basis for c in audience.claims} <= {Basis.USER_PROVIDED, Basis.MODEL_ASSUMPTION}
    assert record.steps["copy"].review_score == 0.75
    critic_requests = [r for r in fake.requests if r.metadata["agent"] == "critic"]
    assert len(critic_requests) == 2
    assert all("campaign brief" in r.messages[1].content for r in critic_requests)


def test_email_step_is_skipped_when_email_is_not_a_channel() -> None:
    engine, _ = _engine()
    brief = BRIEF.model_copy(update={"channels": ["instagram", "tiktok"]})
    result = engine.start(get_template("full-campaign"), {"brief": brief})
    assert result.ok
    assert result.record.steps["email"].skip_reason is SkipReason.CONDITION
    report = result.output("report", CampaignReport)
    assert report.missing_inputs == ["Email sequence"]


def test_optional_content_failure_does_not_sink_the_campaign() -> None:
    def responder(request: CompletionRequest) -> str:
        if request.metadata["agent"] == "social_media":
            return "not json"
        return offline_responder(request)

    engine, _ = _engine(responder)
    result = engine.start(get_template("full-campaign"), {"brief": BRIEF})
    assert result.ok
    assert result.record.steps["social"].status.value == "failed"
    assert "Social calendar" in result.output("report", CampaignReport).missing_inputs


def test_low_critic_score_triggers_one_revision_with_feedback() -> None:
    critic_calls = {"n": 0}

    def responder(request: CompletionRequest) -> str:
        if request.metadata["agent"] == "critic":
            critic_calls["n"] += 1
            if critic_calls["n"] == 1:  # first review of the copy: poor
                return json.dumps(
                    {
                        "scores": {"on_brief": 2, "clarity": 2},
                        "issues": ["headlines are generic"],
                        "instructions": "Lead with the 24-hour cold claim.",
                        "summary": "Needs work.",
                    }
                )
        return offline_responder(request)

    engine, fake = _engine(responder)
    result = engine.start(get_template("ad-pack"), {"brief": BRIEF})
    assert result.ok
    state = result.record.steps["copy"]
    assert state.revisions == 1 and state.review_score == 0.75
    copy_requests = [r for r in fake.requests if r.metadata["agent"] == "copywriter"]
    assert len(copy_requests) == 2
    assert "Lead with the 24-hour cold claim." in copy_requests[1].messages[1].content
    assert result.record.llm_calls == 8  # audience, positioning, copy x2, critic x3, ads


def test_critic_input_wiring_is_validated() -> None:
    registry = default_registry()
    bad = WorkflowDefinition(
        id="bad-review",
        name="Bad review",
        inputs={"brief": BRIEF_KIND, "other": AUDIENCE_KIND},
        steps=(
            StepDefinition(
                id="audience", agent="audience_research", inputs={"brief": "input:brief"}
            ),
            StepDefinition(
                id="pos",
                agent="positioning",
                inputs={"brief": "input:brief", "audience": "step:audience"},
            ),
            StepDefinition(
                id="copy",
                agent="copywriter",
                inputs={"brief": "input:other", "positioning": "step:pos"},
                review=ReviewSpec(critic="critic"),
            ),
        ),
    )
    with pytest.raises(WorkflowValidationError, match="critic input 'brief' expects input.brief@1"):
        validate_definition(bad, registry)


# --------------------------------------------------------------------------- #
# Deterministic checks
# --------------------------------------------------------------------------- #
def _artifact(kind: str, data: dict[str, Any], claims: tuple[Claim, ...] = ()) -> Artifact:
    return Artifact(kind=kind, data=data, claims=claims, produced_by=ProducedBy(agent_id="t"))


def _copy_data(**overrides: Any) -> dict[str, Any]:
    data = CoreCopy(
        tagline="EcoSip: cold all day",
        primary_message="EcoSip keeps drinks cold.",
        headlines=["a", "b", "c"],
        body_copy="Body.",
        call_to_action="Buy",
    ).model_dump()
    data.update(overrides)
    return data


def test_checks_catch_schema_injection_and_grounding_failures() -> None:
    good = check_artifact(_artifact(CORE_COPY_KIND, _copy_data()), BRIEF)
    assert not critical_failures(good) and check_score(good) == 1.0
    broken = check_artifact(_artifact(CORE_COPY_KIND, _copy_data(headlines=[])), BRIEF)
    assert [r.name for r in critical_failures(broken)] == ["schema"]
    leak = check_artifact(
        _artifact(CORE_COPY_KIND, _copy_data(body_copy=f"Sure! {INJECTION_CANARIES[0].upper()}")),
        BRIEF,
    )
    assert "no_injection_echo" in [r.name for r in critical_failures(leak)]
    off_brief = check_artifact(
        _artifact(CORE_COPY_KIND, _copy_data(tagline="Cold", primary_message="Drinks.")), BRIEF
    )
    assert "grounded" in [r.name for r in critical_failures(off_brief)]
    assert 0 < check_score(off_brief) < 1


def test_research_and_report_and_limit_checks() -> None:
    unlabeled = check_artifact(_artifact(AUDIENCE_KIND, {"x": 1}))
    assert {"claims_labeled", "schema"} <= {r.name for r in critical_failures(unlabeled)}
    external = Claim(
        statement="Up 20%.", basis=Basis.EXTERNAL_SOURCE, evidence_ids=("ev-000000000000",)
    )
    flagged = check_artifact(_artifact(AUDIENCE_KIND, {}, (external,)))
    assert "no_unsupported_external_claims" in [r.name for r in critical_failures(flagged)]
    report = check_artifact(
        _artifact(REPORT_KIND, {"title": "t", "markdown": "## Overview\nEcoSip"}), BRIEF
    )
    assert "report_sections" in [r.name for r in critical_failures(report)]
    ads = {"variants": [{"warnings": ["too long"]}, {"warnings": []}]}
    advisory = [
        r for r in check_artifact(_artifact(ADS_KIND, ads)) if r.name == "recommended_limits"
    ]
    assert advisory[0].passed is False and advisory[0].critical is False
    email = check_artifact(_artifact(EMAIL_KIND, {"emails": []}), BRIEF)
    assert any(r.name == "grounded" and not r.critical for r in email)


def test_helper_functions() -> None:
    assert grounded("The Bottle", "a bottle") and not grounded("The Bottle", "the plan")
    assert grounded("\u7eff\u8336", "\u5173\u4e8e\u7eff\u8336") and grounded("The App", "the app")
    assert (
        missing_sections(
            "## Overview\n## Audience\n## Positioning\n## Channel plan\n## Creative\n## Next steps"
        )
        == []
    )
    assert "Overview" in missing_sections("no headings")
    assert text_of({"a": ["x", {"b": "y"}], "n": 3}) == "x\ny\n"
    assert rubric_for(CORE_COPY_KIND)[-1] == "memorability" and rubric_for("x.y@1")[0] == "on_brief"
    assert dumps_compact({"k": "v" * 50}, limit=20).endswith("...")
    assert check_score([]) == 1.0
    review = ReviewResult(score=0.5, summary="s")
    assert review.issues == ()


# --------------------------------------------------------------------------- #
# Regressions found by the Session 3 eval run
# --------------------------------------------------------------------------- #
def test_many_channel_briefs_fit_the_plan_and_ad_contracts() -> None:
    from campaign_forge.agents.schemas import MAX_AD_CHANNELS, ad_channels

    channels = [f"channel {i}" for i in range(20)] + ["Instagram", "Google Ads", "email"]
    picked = ad_channels(channels)
    assert picked[:2] == ["Instagram", "Google Ads"] and len(picked) == MAX_AD_CHANNELS
    engine, fake = _engine()
    brief = BRIEF.model_copy(update={"channels": channels})
    result = engine.start(get_template("full-campaign"), {"brief": brief})
    assert result.ok
    assert {s for s, st in result.record.steps.items() if st.status.value != "succeeded"} == set()
    ad_prompt = (
        next(r for r in fake.requests if r.metadata["agent"] == "ad_copy").messages[1].content
    )
    assert "channel 19" not in ad_prompt.split("Task:")[1]


def test_eval_harness_scores_workflows(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "evals" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval_s3", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eval_s3"] = module
    spec.loader.exec_module(module)
    report = tmp_path / "report.json"
    code = module.main(
        ["--offline", "--workflow", "full-campaign", "--limit", "3", "--report", str(report)]
    )
    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 3 and data["summary"]["llm_calls"] == 36
    assert any(c["name"] == "report.report_sections" for c in data["results"][0]["checks"])
    with pytest.raises(Exception, match="Unknown workflow"):
        module.main(["--offline", "--workflow", "nope"])
    capsys.readouterr()
