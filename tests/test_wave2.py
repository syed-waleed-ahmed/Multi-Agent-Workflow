"""Session 8: wave-2 contracts, agents and templates. Every number here is computed by code."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from campaign_forge.agents.library import (
    BlogWriterAgent,
    CroAdvisorAgent,
    ExperimentDesignerAgent,
    KeywordResearchAgent,
    PerformanceAnalystAgent,
    RepurposerAgent,
    SafetyReviewerAgent,
    SeoOptimizerAgent,
)
from campaign_forge.agents.schemas import BRIEF_KIND, CORE_COPY_KIND
from campaign_forge.agents.schemas_wave2 import (
    ARTICLE_KIND,
    KEYWORDS_KIND,
    LANDING_KIND,
    PERFORMANCE_KIND,
    Article,
    CroAdvice,
    ExperimentPlan,
    FunnelPlan,
    GrowthBacklog,
    KeywordPlan,
    LandingPage,
    PerformanceReport,
    ProductDescriptions,
    RepurposedContent,
    SafetyReview,
    SeoAudit,
    keyword_volumes,
    parse_performance_csv,
    sample_size,
    seo_checks,
    totals,
)
from campaign_forge.config import Settings
from campaign_forge.core import RunContext
from campaign_forge.domain import Artifact, ProducedBy
from campaign_forge.domain.sources import SOURCES_KIND, SourceBundle, SourceDocument
from campaign_forge.evaluation.offline import offline_responder
from campaign_forge.exceptions import AgentError, OutputParsingError
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import WorkflowEngine, default_registry, get_template
from campaign_forge.providers import CompletionRequest, FakeProvider, ProviderRouter

BRIEF = CampaignBrief(
    product_name="EcoSip Bottle",
    product_description="Insulated bottle that keeps drinks cold for 24 hours.",
    target_audience="hikers",
    goal="summer sales",
    tone="fresh",
    channels=["instagram", "email"],
)
CSV = (
    "channel,impressions,clicks,conversions,spend,revenue\n"
    "Instagram,10000,250,10,$120.50,600\n"
    "Email,5000,400,30,0,1500\n"
)


def _ctx(responder: Any = offline_responder) -> tuple[RunContext, FakeProvider]:
    fake = FakeProvider(responder)
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    return RunContext.create(ProviderRouter(settings, providers={"fake": fake})), fake


def _art(kind: str, value: Any) -> Artifact:
    by = ProducedBy(agent_id="test")
    if isinstance(value, dict):
        return Artifact(kind=kind, data=value, produced_by=by)
    return Artifact.from_model(kind, value, produced_by=by)


def _sources(*documents: tuple[str, str]) -> Artifact:
    bundle = SourceBundle(documents=tuple(SourceDocument(name=n, content=c) for n, c in documents))
    return _art(SOURCES_KIND, bundle)


def _offline(agent: str, **kwargs: Any) -> dict[str, Any]:
    request = CompletionRequest.from_prompts("s", "Product name: EcoSip Bottle", **kwargs)
    request = request.model_copy(update={"metadata": {"agent": agent}})
    return json.loads(offline_responder(request))  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Strategy and analytics
# --------------------------------------------------------------------------- #
def test_growth_ice_is_computed_and_ranked_by_code() -> None:
    backlog = GrowthBacklog.model_validate(
        {
            "ideas": [
                {
                    "name": "B",
                    "hypothesis": "h",
                    "channel": "c",
                    "impact": 4,
                    "confidence": 4,
                    "ease": 4,
                    "ice": 9.9,
                },
                {
                    "name": "A",
                    "hypothesis": "h",
                    "channel": "c",
                    "impact": 9,
                    "confidence": 6,
                    "ease": 6,
                },
                {
                    "name": "C",
                    "hypothesis": "h",
                    "channel": "c",
                    "impact": 7,
                    "confidence": 7,
                    "ease": 7,
                },
            ]
        }
    )
    assert [(i.name, i.ice) for i in backlog.ideas] == [("A", 7.0), ("C", 7.0), ("B", 4.0)]
    assert "| 1 | A |" in backlog.to_markdown()


def test_funnel_lead_magnets_must_name_real_stages() -> None:
    plan = _offline("funnel_strategist")
    assert FunnelPlan.model_validate(plan).digest().startswith("Funnel: Awareness")
    plan["lead_magnets"][0]["stage"] = "Nowhere"
    with pytest.raises(ValidationError, match="not one of the funnel's stages"):
        FunnelPlan.model_validate(plan)


def test_sample_sizes_and_measured_baselines() -> None:
    assert sample_size(0.03, 0.2) == 13914  # two-sided, alpha 0.05, power 0.8
    assert sample_size(0.1, 0.1) == 14751
    raw = _offline("experiment_designer")
    assumed = ExperimentPlan.model_validate(raw, context={"measured_rates": {}})
    assert assumed.baseline_source == "assumed" and len(assumed.claims()) == 2
    measured = ExperimentPlan.model_validate(
        raw, context={"measured_rates": {"ctr": 0.05, "cvr": 0.04}}
    )
    assert measured.baseline_source == "measured" and measured.claims() == ()
    assert [e.baseline_rate for e in measured.experiments] == [0.04, 0.05]
    partial = ExperimentPlan.model_validate(raw, context={"measured_rates": {"cvr": 0.04}})
    assert partial.baseline_source == "assumed" and len(partial.claims()) == 1
    claimed = [{**e, "baseline_measured": True} for e in raw["experiments"]]  # a model's claim
    honest = ExperimentPlan.model_validate({"experiments": claimed}, context={"measured_rates": {}})
    assert honest.baseline_source == "assumed"  # the agent path resets every flag
    reread = ExperimentPlan.model_validate(measured.model_dump())  # no context: keeps flags
    assert reread.baseline_source == "measured"
    assert "Baselines are assumptions" in assumed.to_markdown()


def test_performance_csv_metrics_are_computed_by_code() -> None:
    rows = parse_performance_csv(CSV)
    instagram, email = rows
    assert (instagram.ctr, instagram.cvr, instagram.cpc, instagram.cpa) == (
        0.025,
        0.04,
        0.482,
        12.05,
    )
    assert instagram.roas == pytest.approx(4.9793) and email.roas is None  # no spend
    total = totals(rows)
    assert (total.clicks, total.spend, total.revenue) == (650, 120.5, 2100.0)
    no_revenue = parse_performance_csv("campaign,impr,clicks,leads,cost\nA,100,10,1,5")
    assert totals(no_revenue).revenue is None and no_revenue[0].cpa == 5.0
    with pytest.raises(ValueError, match="missing column"):
        parse_performance_csv("channel,clicks\nA,1")
    with pytest.raises(ValueError, match="row 2"):
        parse_performance_csv("channel,impressions,clicks,conversions,spend\nA,x,1,1,1")
    with pytest.raises(ValueError, match="no data rows"):
        parse_performance_csv("channel,impressions,clicks,conversions,spend\n")


def test_performance_analyst_uses_only_user_data() -> None:
    ctx, fake = _ctx()
    sources = _sources(("notes.md", "no table here"), ("ads.csv", CSV))
    output = PerformanceAnalystAgent().run({"sources": sources}, ctx)
    report = output.artifact.as_model(PerformanceReport)
    assert report.source == "ads.csv" and report.total.conversions == 40
    assert "| Total |" in report.to_markdown() and report.recommendations
    assert "BEGIN UNTRUSTED DATA: computed metrics" in fake.requests[0].messages[1].content
    with pytest.raises(AgentError, match="needs performance data"):
        PerformanceAnalystAgent().run({"sources": _sources(("notes.md", "words"))}, _ctx()[0])


def test_experiment_designer_takes_baselines_from_performance() -> None:
    performance = PerformanceReport(
        source="ads.csv",
        rows=parse_performance_csv(CSV),
        total=totals(parse_performance_csv(CSV)),
        summary="s",
        insights=["a", "b"],
        recommendations=["c", "d"],
    )
    ctx, _ = _ctx()
    inputs = {"brief": _art(BRIEF_KIND, BRIEF), "performance": _art(PERFORMANCE_KIND, performance)}
    plan = ExperimentDesignerAgent().run(inputs, ctx).artifact.as_model(ExperimentPlan)
    assert plan.baseline_source == "measured"
    assert {e.baseline_rate for e in plan.experiments} == {0.0615, 0.0433}


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
def test_landing_page_never_invents_proof() -> None:
    page = _offline("landing_page")
    assert LandingPage.model_validate(page).cta_button == "Get yours now"
    page["proof"][0]["text"] = "This bottle changed my life! - Sam"
    with pytest.raises(ValidationError, match="bracketed placeholder"):
        LandingPage.model_validate(page)
    page["proof"] = [{"kind": "statistic", "text": "9 out of 10 hikers agree"}]
    with pytest.raises(ValidationError, match="never invent"):
        LandingPage.model_validate(page)
    page = _offline("landing_page")
    page["headline"] = "x" * 71
    with pytest.raises(ValidationError, match="at most 70"):
        LandingPage.model_validate(page)


def test_product_copy_limits_are_contract_rules() -> None:
    copy = _offline("product_description")
    assert ProductDescriptions.model_validate(copy).digest().startswith("Product copy")
    copy["seo_title"] = "s" * 61
    with pytest.raises(ValidationError, match="at most 60"):
        ProductDescriptions.model_validate(copy)


def test_repurposed_content_respects_platform_limits() -> None:
    content = _offline("repurposer")
    assert len(RepurposedContent.model_validate(content).pieces) == 3
    content["pieces"][0]["content"] = "a" * 281
    with pytest.raises(ValidationError, match="post 1 is 281 characters"):
        RepurposedContent.model_validate(content)
    content = _offline("repurposer")
    content["pieces"][1]["format"] = "x_thread"
    with pytest.raises(ValidationError, match="only once"):
        RepurposedContent.model_validate(content)
    content["pieces"][1] = {"format": "newsletter_blurb", "content": "n" * 1201}
    content["pieces"][2]["format"] = "faq"
    with pytest.raises(ValidationError, match="newsletter_blurb is 1201"):
        RepurposedContent.model_validate(content)
    with pytest.raises(AgentError, match="core copy or your own text"):
        RepurposerAgent().run({"brief": _art(BRIEF_KIND, BRIEF)}, _ctx()[0])
    ctx, _ = _ctx()
    own = {"brief": _art(BRIEF_KIND, BRIEF), "sources": _sources(("post.md", "Our story."))}
    assert RepurposerAgent().run(own, ctx).artifact.kind == "content.repurposed@1"


def test_blog_writer_outlines_then_writes_each_section() -> None:
    ctx, fake = _ctx()
    output = BlogWriterAgent().run({"brief": _art(BRIEF_KIND, BRIEF)}, ctx)
    article = output.artifact.as_model(Article)
    assert len(article.sections) == 3 and len(fake.requests) == 4  # outline + 3 sections
    assert article.word_count > 300 and article.to_markdown().startswith("# How to choose")
    assert fake.requests[0].json_mode and not fake.requests[1].json_mode

    def short(request: CompletionRequest) -> str:
        if "outline a helpful blog article" in request.messages[-1].content:
            return offline_responder(request)
        return "Too short."

    with pytest.raises(OutputParsingError, match="too short"):
        BlogWriterAgent().run({"brief": _art(BRIEF_KIND, BRIEF)}, _ctx(short)[0])


# --------------------------------------------------------------------------- #
# SEO
# --------------------------------------------------------------------------- #
def test_keyword_volumes_come_only_from_user_data() -> None:
    raw = _offline("keyword_research")
    raw["clusters"][0]["volumes"] = {"best ecosip bottle": 999999}  # a model guess
    plan = KeywordPlan.model_validate(raw, context={"keyword_volumes": {}})
    assert all(c.volumes == {} for c in plan.clusters) and plan.volume_source.startswith("none")
    supplied = KeywordPlan.model_validate(
        raw, context={"keyword_volumes": {"best ecosip bottle": 1200}, "volume_source": "user CSV"}
    )
    assert supplied.clusters[0].volumes == {"best ecosip bottle": 1200}
    assert "best ecosip bottle (1,200)" in supplied.to_markdown()
    assert keyword_volumes('keyword,volume\nA,"3,400"\nB,n/a') == {"a": 3400}
    assert keyword_volumes("term,clicks\nA,1") == {}

    ctx, _ = _ctx()
    sources = _sources(("kw.csv", "keyword,volume\nbest ecosip bottle,880"))
    output = KeywordResearchAgent().run({"brief": _art(BRIEF_KIND, BRIEF), "sources": sources}, ctx)
    with_csv = output.artifact.as_model(KeywordPlan)
    assert with_csv.clusters[0].volumes == {"best ecosip bottle": 880}
    assert with_csv.volume_source == "user CSV: kw.csv" and "volumes from" in output.notes[0]


def test_seo_checks_and_audits() -> None:
    body = "Intro about insulated bottles.\n\n## Insulated bottles\n\ntext\n\n## Care\n\nmore"
    checks = {
        c.name: c
        for c in seo_checks("Insulated bottles guide for hikers", "", body, "insulated bottles")
    }
    assert checks["title_length"].passed and not checks["meta_description"].passed
    assert checks["subheadings"].passed and not checks["word_count"].passed
    assert checks["keyword_in_subheading"].passed and checks["keyword_early"].passed
    assert len(seo_checks("t", "m", body, None)) == 4

    ctx, _ = _ctx()
    article = Article.model_validate(
        {
            "title": "How to choose an insulated bottle",
            "meta_description": "m" * 100,
            "target_keyword": "insulated bottle",
            "sections": [{"heading": "Why an insulated bottle", "body": "insulated bottle " * 10}],
        }
    )
    audit = SeoOptimizerAgent().run({"article": _art(ARTICLE_KIND, article)}, ctx)
    result = audit.artifact.as_model(SeoAudit)
    assert result.target_keyword == "insulated bottle" and 0 < result.score < 1
    assert result.recommendations and "checks failed" in audit.notes[0]

    page = _sources(("page.md", "# EcoSip landing\n\nBuy EcoSip today."))
    keywords = _art(KEYWORDS_KIND, KeywordPlan.model_validate(_offline("keyword_research")))
    from_page = SeoOptimizerAgent().run({"sources": page, "keywords": keywords}, _ctx()[0])
    assert from_page.artifact.as_model(SeoAudit).page == "page.md"
    with pytest.raises(AgentError, match="article or your own page"):
        SeoOptimizerAgent().run({}, _ctx()[0])


# --------------------------------------------------------------------------- #
# Quality
# --------------------------------------------------------------------------- #
def test_safety_review_keeps_only_model_flags_that_quote_the_content() -> None:
    copy = {
        "tagline": "The best bottle ever",
        "primary_message": "Clinically proven to keep you hydrated.",
        "headlines": ["Eco-friendly and bold", "For everyone", "Built to last"],
        "body_copy": "Only for young athletes. Risk-free trial.",
        "call_to_action": "Buy now",
    }

    def answer(request: CompletionRequest) -> str:
        return json.dumps(
            {
                "flags": [
                    {
                        "category": "targeting",
                        "deliverable": "copy",
                        "quote": "only for  young athletes",
                        "note": "excludes others",
                    },
                    {
                        "category": "health",
                        "deliverable": "copy",
                        "quote": "cures dehydration",
                        "note": "not in the copy",
                    },
                    {"category": "other", "deliverable": "ads", "quote": "Buy now", "note": "x"},
                ]
            }
        )

    ctx, _ = _ctx(answer)
    output = SafetyReviewerAgent().run({"copy": _art(CORE_COPY_KIND, copy)}, ctx)
    review = output.artifact.as_model(SafetyReview)
    by_source = {(f.source, f.category) for f in review.flags}
    assert {
        ("rule", "superlative"),
        ("rule", "health"),
        ("rule", "environmental"),
        ("rule", "finance"),
        ("model", "targeting"),
    } == by_source
    assert review.dropped_model_flags == 2 and not review.passed
    assert (
        "not legal advice" in review.to_markdown() and "2 model flag(s) dropped" in output.notes[1]
    )
    clean = SafetyReview(checked=["copy"], flags=[])
    assert clean.passed and "No risky claims" in clean.to_markdown()
    with pytest.raises(AgentError, match="at least one deliverable"):
        SafetyReviewerAgent().run({}, _ctx()[0])


def test_cro_checklist_is_computed_and_advice_prioritised() -> None:
    page = LandingPage.model_validate(_offline("landing_page"))
    ctx, _ = _ctx()
    advice = CroAdvisorAgent().run({"landing": _art(LANDING_KIND, page)}, ctx).artifact
    result = advice.as_model(CroAdvice)
    checks = {c.name: c.passed for c in result.checks}
    assert checks["proof_supplied"] is False and checks["benefits"] is True
    assert result.score == round(sum(checks.values()) / len(checks), 3)
    assert [r.impact for r in result.recommendations] == ["high", "medium"]
    reordered = CroAdvice(
        checks=[],
        score=0.5,
        recommendations=[
            {"change": "a", "reason": "r", "impact": "low"},  # type: ignore[list-item]
            {"change": "b", "reason": "r", "impact": "high"},
        ],
    )
    assert [r.change for r in reordered.recommendations] == ["b", "a"]


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def _engine() -> WorkflowEngine:
    fake = FakeProvider(offline_responder)
    settings = Settings(_env_file=None, provider="fake")  # type: ignore[call-arg]
    return WorkflowEngine(default_registry(), ProviderRouter(settings, providers={"fake": fake}))


def test_seo_pack_and_performance_review_end_to_end() -> None:
    engine = _engine()
    seo = engine.start(get_template("seo-content-pack"), {"brief": BRIEF})
    assert seo.ok and set(seo.record.outputs()) == {
        "keywords",
        "plan",
        "article",
        "audit",
        "safety",
    }
    assert seo.record.outputs()["article"].as_model(Article).target_keyword

    without = engine.start(get_template("performance-review"), {"brief": BRIEF})
    assert without.ok and without.record.steps["performance"].skip_reason is not None
    with_data = engine.start(
        get_template("performance-review"),
        {
            "brief": BRIEF,
            "sources": SourceBundle(documents=(SourceDocument(name="ads.csv", content=CSV),)),
        },
    )
    assert with_data.ok
    plan = with_data.record.outputs()["experiments"].as_model(ExperimentPlan)
    assert plan.baseline_source == "measured"
