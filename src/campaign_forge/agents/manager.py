"""Manager agent - synthesises every prior output into a final campaign brief."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from ..exceptions import OutputParsingError
from ..models import CampaignBrief, CopyContent
from .base import BaseAgent

#: Required sections and the heading keywords that count as each (case-insensitive).
REQUIRED_SECTIONS: dict[str, str] = {
    "Overview": r"overview|summary|introduction",
    "Target Audience & Insights": r"audience|insight",
    "Positioning & Key Messages": r"position|messag",
    "Example Headlines & Copy": r"headline|copy",
    "Visual Direction": r"visual|creative|imagery|design",
    "Deliverables": r"deliverable|asset|next step",
}
#: Below this, a "brief" is not a brief (e.g. the model answered "ok").
MIN_BRIEF_WORDS = 60
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class BriefCheck:
    """Structural review of a final brief."""

    headings: tuple[str, ...]
    word_count: int
    missing_sections: tuple[str, ...]

    @property
    def degenerate(self) -> bool:
        """True when the output cannot be a usable brief at all."""
        return not self.headings or self.word_count < MIN_BRIEF_WORDS

    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.headings:
            issues.append("it has no Markdown section headings")
        if self.word_count < MIN_BRIEF_WORDS:
            issues.append(f"it is only {self.word_count} words long")
        if self.missing_sections:
            issues.append("it is missing these sections: " + ", ".join(self.missing_sections))
        return issues


def check_brief(text: str) -> BriefCheck:
    """Check a brief for headings, length and the required sections."""
    headings = tuple(h.strip() for h in _HEADING_RE.findall(text))
    heading_text = "\n".join(headings).lower()
    missing = tuple(
        section
        for section, pattern in REQUIRED_SECTIONS.items()
        if not re.search(pattern, heading_text)
    )
    return BriefCheck(headings=headings, word_count=len(text.split()), missing_sections=missing)


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
        """Return the final campaign brief as Markdown.

        The output is checked for structure (defect D4). A response with problems
        is re-requested once, with the problems listed. If the second answer is
        still degenerate (no headings, or too short to be a brief), the step
        fails. If it is merely missing a section, it is accepted and a warning is
        logged.

        Raises:
            OutputParsingError: if the brief is still degenerate after one repair attempt.
        """
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
            f"{self._data(brief.as_prompt_context(), 'campaign brief')}\n\n"
            "Research summary:\n"
            f"{self._data(research_summary, 'research summary')}\n\n"
            "Copy:\n"
            f"{self._data(copy_block, 'copy')}\n\n"
            "Image prompts:\n"
            f"{self._data(image_block, 'image prompts')}\n\n"
            "Task: create a final campaign brief in Markdown with these sections, each "
            "as a Markdown heading:\n"
            "1. Overview\n"
            "2. Target Audience & Insights\n"
            "3. Positioning & Key Messages (include tagline and primary message)\n"
            "4. Example Headlines & Copy\n"
            "5. Visual Direction (summarise the image prompts)\n"
            "6. Deliverables (concrete assets to produce)\n\n"
            "Be concise but specific. Write it as if handing it to a marketing and "
            "design team."
        )
        result = self._generate(user_prompt)
        check = check_brief(result)
        if check.degenerate or check.missing_sections:
            self._log.warning("Manager brief needs repair: %s", "; ".join(check.problems()))
            repair = (
                "Your previous answer cannot be used because "
                + "; ".join(check.problems())
                + ". Write the complete brief again with all six sections as Markdown headings."
            )
            result = self._generate(f"{user_prompt}\n\n{repair}")
            check = check_brief(result)
        if check.degenerate:
            raise OutputParsingError(
                self.name,
                "Final brief is unusable after a repair attempt: " + "; ".join(check.problems()),
            )
        if check.missing_sections:
            self._log.warning(
                "Final brief accepted without section(s): %s", ", ".join(check.missing_sections)
            )
        return result
