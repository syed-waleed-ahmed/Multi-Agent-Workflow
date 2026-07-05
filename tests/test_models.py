"""Tests for the domain models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from campaign_forge.models import CampaignBrief, CampaignResult, CopyContent, slugify


class TestCampaignBrief:
    def test_channels_from_comma_string(self) -> None:
        brief = CampaignBrief(
            product_name="P",
            product_description="D",
            target_audience="A",
            goal="G",
            tone="T",
            channels="instagram, tiktok , email",  # type: ignore[arg-type]
        )
        assert brief.channels == ["instagram", "tiktok", "email"]

    def test_channels_deduplicated_case_insensitively(self) -> None:
        brief = CampaignBrief(
            product_name="P",
            product_description="D",
            target_audience="A",
            goal="G",
            tone="T",
            channels=["Email", "email", "  ", "Instagram"],
        )
        assert brief.channels == ["Email", "Instagram"]

    def test_empty_channels_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CampaignBrief(
                product_name="P",
                product_description="D",
                target_audience="A",
                goal="G",
                tone="T",
                channels=["", "   "],
            )

    def test_blank_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CampaignBrief(
                product_name="   ",
                product_description="D",
                target_audience="A",
                goal="G",
                tone="T",
                channels=["email"],
            )

    def test_brief_is_immutable(self, sample_brief: CampaignBrief) -> None:
        with pytest.raises(ValidationError):
            sample_brief.product_name = "changed"  # type: ignore[misc]

    def test_as_prompt_context_contains_fields(self, sample_brief: CampaignBrief) -> None:
        context = sample_brief.as_prompt_context()
        assert "EcoSip" in context
        assert "instagram, email" in context


class TestCopyContent:
    def test_single_headline_string_coerced_to_list(self) -> None:
        copy = CopyContent(
            tagline="t",
            primary_message="m",
            headlines="only one",  # type: ignore[arg-type]
            body_copy="b",
            call_to_action="cta",
        )
        assert copy.headlines == ["only one"]

    def test_missing_required_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CopyContent.model_validate({"tagline": "t"})

    def test_blank_headlines_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CopyContent(
                tagline="t",
                primary_message="m",
                headlines=["", "  "],
                body_copy="b",
                call_to_action="cta",
            )


class TestCampaignResult:
    def test_to_markdown_contains_appendices(self, sample_result: CampaignResult) -> None:
        md = sample_result.to_markdown()
        assert "# Campaign Brief" in md
        assert "Appendix A" in md
        assert "Appendix C" in md
        assert sample_result.marketing_copy.tagline in md

    def test_to_dict_is_json_serialisable(self, sample_result: CampaignResult) -> None:
        payload = sample_result.to_dict()
        json.dumps(payload)  # must not raise
        assert payload["model"] == "fake-model"
        assert payload["marketing_copy"]["tagline"]

    def test_save_writes_md_and_json(self, sample_result: CampaignResult, tmp_path: Path) -> None:
        md_path = sample_result.save(tmp_path)
        assert md_path.exists()
        assert md_path.suffix == ".md"
        json_path = md_path.with_suffix(".json")
        assert json_path.exists()
        reloaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert reloaded["brief"]["product_name"] == sample_result.brief.product_name

    def test_generated_at_is_timezone_aware(self, sample_result: CampaignResult) -> None:
        assert sample_result.generated_at.tzinfo is not None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("EcoSip Reusable Bottle", "ecosip-reusable-bottle"),
        ("  Hello!! World  ", "hello-world"),
        ("***", "campaign"),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    assert slugify(value) == expected
