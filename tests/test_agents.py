"""Tests for the individual agents (using the FakeLLM)."""

from __future__ import annotations

import pytest

from campaign_forge.agents import (
    ArtDirectorAgent,
    BaseAgent,
    CopywriterAgent,
    ManagerAgent,
    ResearchAgent,
)
from campaign_forge.exceptions import OutputParsingError
from campaign_forge.models import CampaignBrief, CopyContent

from .conftest import FakeLLM


def test_research_agent_returns_text(sample_brief: CampaignBrief, fake_llm: FakeLLM) -> None:
    result = ResearchAgent(fake_llm).run(sample_brief)  # type: ignore[arg-type]
    assert "Audience" in result
    assert fake_llm.calls[0][2] is False  # research does not use json mode


def test_copywriter_returns_validated_copy(sample_brief: CampaignBrief, fake_llm: FakeLLM) -> None:
    copy = CopywriterAgent(fake_llm).run(sample_brief, "research")  # type: ignore[arg-type]
    assert isinstance(copy, CopyContent)
    assert copy.tagline == "Sip Smart, Live Green"
    assert fake_llm.calls[0][2] is True  # copywriter uses json mode


def test_copywriter_raises_on_bad_json(sample_brief: CampaignBrief) -> None:
    class BadLLM(FakeLLM):
        def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
            return "this is not json"

    with pytest.raises(OutputParsingError) as excinfo:
        CopywriterAgent(BadLLM()).run(sample_brief, "research")  # type: ignore[arg-type]
    assert excinfo.value.agent == "copywriter"


def test_copywriter_raises_on_incomplete_json(sample_brief: CampaignBrief) -> None:
    class PartialLLM(FakeLLM):
        def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
            return '{"tagline": "only tagline"}'

    with pytest.raises(OutputParsingError):
        CopywriterAgent(PartialLLM()).run(sample_brief, "research")  # type: ignore[arg-type]


def test_art_director_parses_json(
    sample_brief: CampaignBrief, sample_copy: CopyContent, fake_llm: FakeLLM
) -> None:
    prompts = ArtDirectorAgent(fake_llm).run(sample_brief, "research", sample_copy)  # type: ignore[arg-type]
    assert len(prompts) == 2
    assert all(isinstance(p, str) and p for p in prompts)


def test_art_director_falls_back_to_line_parsing(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    class LinesLLM(FakeLLM):
        def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
            return (
                "1. A vivid beach scene with a teal bottle in the sun\n"
                "2. Minimalist studio shot on a clean white background"
            )

    prompts = ArtDirectorAgent(LinesLLM()).run(sample_brief, "research", sample_copy)  # type: ignore[arg-type]
    assert len(prompts) == 2
    assert prompts[0].startswith("A vivid beach")


def test_manager_returns_markdown(
    sample_brief: CampaignBrief, sample_copy: CopyContent, fake_llm: FakeLLM
) -> None:
    brief = ManagerAgent(fake_llm).run(sample_brief, "research", sample_copy, ["p1"])  # type: ignore[arg-type]
    assert brief.startswith("# Campaign Brief")


def test_base_agent_requires_name_and_prompt() -> None:
    with pytest.raises(TypeError, match="must define class attribute"):

        class BrokenAgent(BaseAgent):
            pass
