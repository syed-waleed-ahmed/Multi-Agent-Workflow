"""Manager agent - synthesises every prior output into a final campaign brief."""

from __future__ import annotations

from typing import ClassVar

from ..models import CampaignBrief, CopyContent
from .base import BaseAgent


class ManagerAgent(BaseAgent):
    """Assembles the research, copy and visuals into a polished Markdown brief."""

    name: ClassVar[str] = "manager"
    system_prompt: ClassVar[str] = (
        "You are a Campaign Manager Agent. You synthesise research, copy and visual "
        "directions into a clear campaign brief that marketers and designers can "
        "act on immediately. You write clean, well-structured Markdown."
    )

    def run(
        self,
        brief: CampaignBrief,
        research_summary: str,
        copy: CopyContent,
        image_prompts: list[str],
    ) -> str:
        """Return the final campaign brief as Markdown."""
        headlines = "\n".join(f"  - {h}" for h in copy.headlines) or "  - (none)"
        image_block = "\n".join(f"- {p}" for p in image_prompts) if image_prompts else "None"
        copy_block = (
            f"- Tagline: {copy.tagline}\n"
            f"- Primary message: {copy.primary_message}\n"
            f"- Headlines:\n{headlines}\n"
            f"- Body copy: {copy.body_copy}\n"
            f"- Call to action: {copy.call_to_action}"
        )
        user_prompt = (
            "Original campaign brief:\n"
            f"{brief.as_prompt_context()}\n\n"
            "Research summary:\n"
            f"{research_summary}\n\n"
            "Copy:\n"
            f"{copy_block}\n\n"
            "Image prompts:\n"
            f"{image_block}\n\n"
            "Task: create a final campaign brief in Markdown with these sections:\n"
            "1. Overview\n"
            "2. Target Audience & Insights\n"
            "3. Positioning & Key Messages (include tagline and primary message)\n"
            "4. Example Headlines & Copy\n"
            "5. Visual Direction (summarise the image prompts)\n"
            "6. Deliverables (concrete assets to produce)\n\n"
            "Be concise but specific. Write it as if handing it to a marketing and "
            "design team."
        )
        return self._generate(user_prompt)
