"""Tests for the workflow orchestrator, including concurrent batch behaviour."""

from __future__ import annotations

from campaign_forge.config import Settings
from campaign_forge.models import CampaignBrief, CampaignResult
from campaign_forge.workflow import BatchItemResult, CampaignWorkflow

from .conftest import FakeLLM


def _brief(name: str) -> CampaignBrief:
    return CampaignBrief(
        product_name=name,
        product_description="A great product.",
        target_audience="everyone",
        goal="sales",
        tone="fun",
        channels=["email"],
    )


def _workflow(settings: Settings, llm: FakeLLM) -> CampaignWorkflow:
    return CampaignWorkflow(settings, llm=llm)  # type: ignore[arg-type]


def test_run_produces_complete_result(
    settings: Settings, fake_llm: FakeLLM, sample_brief: CampaignBrief
) -> None:
    result = _workflow(settings, fake_llm).run(sample_brief)
    assert isinstance(result, CampaignResult)
    assert result.research_summary
    assert result.marketing_copy.tagline == "Sip Smart, Live Green"
    assert len(result.image_prompts) == 2
    assert result.final_brief.startswith("# Campaign Brief")
    assert result.model == "fake-model"
    assert result.duration_seconds >= 0.0


def test_run_calls_all_four_agents(
    settings: Settings, fake_llm: FakeLLM, sample_brief: CampaignBrief
) -> None:
    _workflow(settings, fake_llm).run(sample_brief)
    systems = [call[0].lower() for call in fake_llm.calls]
    assert any("research agent" in s for s in systems)
    assert any("copywriter" in s for s in systems)
    assert any("art director" in s for s in systems)
    assert any("campaign manager" in s for s in systems)


def test_batch_preserves_order(settings: Settings, fake_llm: FakeLLM) -> None:
    briefs = [_brief(f"Product {i}") for i in range(5)]
    results = _workflow(settings, fake_llm).run_batch(briefs)
    assert [item.index for item in results] == [0, 1, 2, 3, 4]
    assert all(item.ok for item in results)
    assert [item.brief.product_name for item in results] == [b.product_name for b in briefs]


def test_batch_isolates_failures(settings: Settings) -> None:
    failing_llm = FakeLLM(fail_on="BrokenProduct")
    briefs = [_brief("GoodOne"), _brief("BrokenProduct"), _brief("GoodTwo")]
    results = _workflow(settings, failing_llm).run_batch(briefs)

    by_name = {item.brief.product_name: item for item in results}
    assert by_name["GoodOne"].ok
    assert by_name["GoodTwo"].ok
    assert not by_name["BrokenProduct"].ok
    assert by_name["BrokenProduct"].error is not None
    assert by_name["BrokenProduct"].result is None


def test_batch_invokes_callback(settings: Settings, fake_llm: FakeLLM) -> None:
    seen: list[int] = []
    briefs = [_brief(f"P{i}") for i in range(3)]
    _workflow(settings, fake_llm).run_batch(briefs, on_result=lambda item: seen.append(item.index))
    assert sorted(seen) == [0, 1, 2]


def test_empty_batch_returns_empty_list(settings: Settings, fake_llm: FakeLLM) -> None:
    assert _workflow(settings, fake_llm).run_batch([]) == []


def test_batch_item_result_ok_property() -> None:
    ok_item = BatchItemResult(index=0, brief=_brief("X"), result=None, error="boom")
    assert ok_item.ok is False
