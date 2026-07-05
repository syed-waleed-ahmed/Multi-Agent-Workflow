"""Art director agent - proposes visual concepts as image-generation prompts."""

from __future__ import annotations

from typing import ClassVar

from ..models import CampaignBrief, CopyContent
from ..parsing import parse_json_object, parse_prompt_lines
from .base import BaseAgent


class ArtDirectorAgent(BaseAgent):
    """Produces a list of ready-to-use image prompts (DALL-E / Stable Diffusion)."""

    name: ClassVar[str] = "art_director"
    system_prompt: ClassVar[str] = (
        "You are an Art Director creating image prompts for AI image generators "
        "such as DALL-E or Stable Diffusion. You translate marketing ideas into "
        "clear, single-line visual prompts. You respond with a single JSON object."
    )

    def run(
        self,
        brief: CampaignBrief,
        research_summary: str,
        copy: CopyContent,
    ) -> list[str]:
        """Return 3-5 image prompts. Robust to non-JSON responses.

        The model is asked for JSON (``{"image_prompts": [...]}``); if it ignores
        that, we fall back to parsing a numbered/bulleted list so a formatting
        slip never loses the whole result.
        """
        user_prompt = (
            "Campaign brief:\n"
            f"{brief.as_prompt_context()}\n\n"
            "Key messaging:\n"
            f"- Tagline: {copy.tagline}\n"
            f"- Primary message: {copy.primary_message}\n\n"
            "Research summary:\n"
            f"{research_summary}\n\n"
            "Task: propose 3-5 distinct visual concepts for the campaign. For each "
            "concept, write a single-line prompt for an image model that includes "
            "subject, style, mood, colours and key details.\n\n"
            'Return a JSON object of the form {"image_prompts": ["prompt one", ...]}.'
        )
        raw = self._generate(user_prompt, json_mode=True)
        prompts = self._extract_prompts(raw)
        if not prompts:
            self._log.warning("Art director returned no usable prompts.")
        return prompts

    def _extract_prompts(self, raw: str) -> list[str]:
        """Recover a clean list of prompt strings from the raw model output."""
        try:
            data = parse_json_object(raw)
            candidate = data.get("image_prompts", data.get("prompts", []))
            if isinstance(candidate, list):
                prompts = [str(item).strip() for item in candidate if str(item).strip()]
                if prompts:
                    return prompts
        except ValueError:
            self._log.debug("Art director output was not JSON; falling back to line parsing.")
        return parse_prompt_lines(raw)
