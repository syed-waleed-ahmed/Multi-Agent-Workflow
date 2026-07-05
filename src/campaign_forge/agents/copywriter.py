"""Copywriter agent - turns research into structured, validated campaign copy."""

from __future__ import annotations

from typing import ClassVar

from pydantic import ValidationError

from ..exceptions import OutputParsingError
from ..models import CampaignBrief, CopyContent
from ..parsing import parse_json_object
from .base import BaseAgent

#: How many times to re-ask the model to fix malformed output before giving up.
#: Weaker/cheaper models occasionally emit not-quite-valid JSON; a single
#: corrective retry recovers most of those without failing the whole campaign.
_MAX_REPAIR_ATTEMPTS = 1


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

        The first response is parsed and validated; if that fails, the model is
        re-asked up to :data:`_MAX_REPAIR_ATTEMPTS` times with the parse error
        fed back to it, so a single malformed response does not abort the run.

        Raises:
            OutputParsingError: if the output still cannot be parsed/validated
                after the repair attempts are exhausted.
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

        prompt = user_prompt
        last_error: Exception | None = None
        for attempt in range(_MAX_REPAIR_ATTEMPTS + 1):
            raw = self._generate(prompt, json_mode=True)
            try:
                return CopyContent.model_validate(parse_json_object(raw))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                self._log.warning(
                    "Copywriter output invalid (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_REPAIR_ATTEMPTS + 1,
                    exc,
                )
                prompt = f"{user_prompt}\n\n{self._repair_instruction(exc)}"

        raise OutputParsingError(
            self.name, f"Could not parse structured copy from model output: {last_error}"
        ) from last_error

    @staticmethod
    def _repair_instruction(error: Exception) -> str:
        """A corrective note appended to the prompt after a failed parse."""
        return (
            "Your previous response could not be parsed as the required JSON object "
            f"(error: {error}). Respond again with ONLY a single valid JSON object "
            "containing exactly the required keys and nothing else."
        )
