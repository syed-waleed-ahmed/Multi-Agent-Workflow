"""Research agent - analyses the brief and surfaces audience insights."""

from __future__ import annotations

from typing import ClassVar

from ..models import CampaignBrief
from .base import BaseAgent


class ResearchAgent(BaseAgent):
    """Produces a structured research summary from a campaign brief."""

    name: ClassVar[str] = "research"
    system_prompt: ClassVar[str] = (
        "You are a Marketing Research Agent. Given a campaign brief, you analyse "
        "the target audience, their pain points, current market trends and "
        "competitor angles. Respond with a structured summary using clear "
        "headings and concise bullet points."
    )

    def run(self, brief: CampaignBrief) -> str:
        """Return a Markdown research summary for the given brief."""
        user_prompt = (
            "Campaign brief:\n"
            f"{brief.as_prompt_context()}\n\n"
            "Task:\n"
            "1. Describe the target audience (demographics + interests).\n"
            "2. List their main pain points and desires.\n"
            "3. Mention a few relevant market or content trends.\n"
            "4. Suggest 3-5 positioning angles we could use in the campaign."
        )
        return self._generate(user_prompt)
