"""Shared test fixtures and lightweight fakes.

Every test runs fully offline: no real API key and no network access are ever
required. The ``FakeLLM`` duck-types :class:`campaign_forge.llm.LLMClient` and
returns deterministic, agent-specific canned responses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_forge.config import Settings
from campaign_forge.models import CampaignBrief, CampaignResult, CopyContent

RESEARCH_TEXT = "## Audience\n- Eco-conscious professionals\n\n## Trends\n- Reusable everything"
COPY_PAYLOAD = {
    "tagline": "Sip Smart, Live Green",
    "primary_message": "A bottle that keeps drinks cold and the planet cooler.",
    "headlines": ["Cold for 24 hours", "Zero plastic, zero compromise", "Your daily eco habit"],
    "body_copy": "Meet the bottle designed for people who care about their drink and the planet.",
    "call_to_action": "Shop the summer drop",
}
ART_PAYLOAD = {
    "image_prompts": [
        "A sleek insulated bottle on a sunlit beach, vibrant teal palette, energetic mood",
        "Studio product shot of the bottle with condensation, minimalist white background",
    ]
}
MANAGER_TEXT = "# Campaign Brief\n\n## Overview\nA fresh summer push for the EcoSip bottle."


class FakeLLM:
    """A stand-in for :class:`LLMClient` that returns canned, routed responses."""

    def __init__(self, *, model: str = "fake-model", fail_on: str | None = None) -> None:
        self.model = model
        self._fail_on = fail_on
        self.calls: list[tuple[str, str, bool]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        from campaign_forge.exceptions import LLMError

        self.calls.append((system_prompt, user_prompt, json_mode))
        if self._fail_on and self._fail_on in user_prompt:
            raise LLMError(f"Simulated failure for {self._fail_on!r}")

        system = system_prompt.lower()
        if "research agent" in system:
            return RESEARCH_TEXT
        if "copywriter" in system:
            return json.dumps(COPY_PAYLOAD)
        if "art director" in system:
            return json.dumps(ART_PAYLOAD)
        if "campaign manager" in system:
            return MANAGER_TEXT
        raise AssertionError(f"Unexpected system prompt: {system_prompt!r}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Hermetic settings that never read the real environment or .env file."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        groq_api_key="test-key",
        output_dir=tmp_path / "outputs",
        max_retries=1,
        max_workers=2,
        log_level="WARNING",
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def sample_brief() -> CampaignBrief:
    return CampaignBrief(
        product_name="EcoSip Reusable Bottle",
        product_description="Insulated bottle that keeps drinks cold for 24 hours.",
        target_audience="eco-conscious young professionals",
        goal="drive summer sales",
        tone="fresh and energetic",
        channels=["instagram", "email"],
    )


@pytest.fixture
def sample_copy() -> CopyContent:
    return CopyContent.model_validate(COPY_PAYLOAD)


@pytest.fixture
def sample_result(sample_brief: CampaignBrief, sample_copy: CopyContent) -> CampaignResult:
    return CampaignResult(
        brief=sample_brief,
        research_summary=RESEARCH_TEXT,
        marketing_copy=sample_copy,
        image_prompts=list(ART_PAYLOAD["image_prompts"]),
        final_brief=MANAGER_TEXT,
        model="fake-model",
        duration_seconds=1.5,
    )
