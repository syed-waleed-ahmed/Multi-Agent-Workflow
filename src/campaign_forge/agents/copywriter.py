"""Copywriter agent - turns research into structured, validated campaign copy."""

from __future__ import annotations

from typing import ClassVar

from pydantic import ValidationError

from ..exceptions import OutputParsingError
from ..models import CampaignBrief, CopyContent
from ..parsing import parse_json_object
from .base import BaseAgent


class CopywriterAgent(BaseAgent):
    """Generates a :class:`CopyContent` object grounded in the research summary."""

    name: ClassVar[str] = "copywriter"
    system_prompt: ClassVar[str] = (
        "You are a creative copywriter for digital marketing campaigns. You write "
        "concise, persuasive copy grounded in the research provided. You always "
        "respond with a single valid JSON object and no surrounding prose."
    )

    def run(self, brief: CampaignBrief, research_summary: str) -> CopyContent:
        """Return validated campaign copy.

        Raises:
            OutputParsingError: if the model output cannot be parsed/validated
                into a :class:`CopyContent`.
        """
        user_prompt = (
            "Campaign brief:\n"
            f"{brief.as_prompt_context()}\n\n"
            "Research summary:\n"
            f"{research_summary}\n\n"
            "Task: create campaign copy and return it as a JSON object with EXACTLY "
            "these keys:\n"
            '- "tagline": one short catchy line.\n'
            '- "primary_message": 2-3 sentences summarising the campaign message.\n'
            '- "headlines": an array of 3-5 short ad headlines.\n'
            '- "body_copy": a paragraph suitable for a landing page or social caption.\n'
            '- "call_to_action": a strong CTA phrase.\n\n'
            "Return ONLY valid JSON, no extra text."
        )
        raw = self._generate(user_prompt, json_mode=True)
        try:
            data = parse_json_object(raw)
            return CopyContent.model_validate(data)
        except (ValueError, ValidationError) as exc:
            self._log.warning("Failed to parse copywriter output: %s", exc)
            raise OutputParsingError(
                self.name, f"Could not parse structured copy from model output: {exc}"
            ) from exc
