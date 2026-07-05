"""Typed, validated domain models.

These Pydantic models are the contract between the CLI, the agents and the
orchestrator. Using them (instead of passing raw ``dict`` objects around) means
malformed input is rejected at the boundary and every downstream consumer gets
strongly-typed, already-validated data.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# A string that is trimmed of surrounding whitespace and must not be empty.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Upper bounds on brief fields. These are generous for realistic input but stop a
# pathological brief (a pasted document, thousands of channels) from ballooning
# every prompt, blowing past the model context window, and running up API cost.
MAX_NAME_LEN = 200
MAX_LINE_LEN = 500
MAX_DESCRIPTION_LEN = 4000
MAX_CHANNELS = 25
MAX_CHANNEL_LEN = 60

# A single channel label: trimmed, non-empty, and length-bounded.
ChannelStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHANNEL_LEN)
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = 60) -> str:
    """Convert an arbitrary string into a filesystem-safe slug."""
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or "campaign"


class CampaignBrief(BaseModel):
    """The user-supplied input that drives a campaign generation run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_name: NonEmptyStr = Field(max_length=MAX_NAME_LEN)
    product_description: NonEmptyStr = Field(max_length=MAX_DESCRIPTION_LEN)
    target_audience: NonEmptyStr = Field(max_length=MAX_LINE_LEN)
    goal: NonEmptyStr = Field(max_length=MAX_LINE_LEN)
    tone: NonEmptyStr = Field(max_length=MAX_LINE_LEN)
    channels: list[ChannelStr] = Field(min_length=1, max_length=MAX_CHANNELS)

    @field_validator("channels", mode="before")
    @classmethod
    def _coerce_channels(cls, value: object) -> list[str]:
        """Accept a list or a comma-separated string; trim, drop blanks, dedupe."""
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
        else:
            raise TypeError("channels must be a list of strings or a comma-separated string")

        seen: set[str] = set()
        cleaned: list[str] = []
        for item in items:
            normalised = item.strip()
            key = normalised.lower()
            if normalised and key not in seen:
                seen.add(key)
                cleaned.append(normalised)
        if not cleaned:
            raise ValueError("At least one non-empty channel is required.")
        return cleaned

    def as_prompt_context(self) -> str:
        """Render the brief as a bullet list for inclusion in an LLM prompt."""
        return (
            f"- Product name: {self.product_name}\n"
            f"- Product description: {self.product_description}\n"
            f"- Target audience: {self.target_audience}\n"
            f"- Goal: {self.goal}\n"
            f"- Tone: {self.tone}\n"
            f"- Channels: {', '.join(self.channels)}"
        )


class CopyContent(BaseModel):
    """Structured marketing copy produced by the copywriter agent."""

    model_config = ConfigDict(extra="ignore")

    tagline: NonEmptyStr
    primary_message: NonEmptyStr
    headlines: list[str] = Field(min_length=1)
    body_copy: NonEmptyStr
    call_to_action: NonEmptyStr

    @field_validator("headlines", mode="before")
    @classmethod
    def _coerce_headlines(cls, value: object) -> list[str]:
        """Accept either a single string or a list; keep only non-empty entries."""
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise TypeError("headlines must be a string or list of strings")
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("At least one non-empty headline is required.")
        return cleaned


class CampaignResult(BaseModel):
    """The complete output of a single campaign generation run."""

    model_config = ConfigDict(extra="forbid")

    brief: CampaignBrief
    research_summary: str
    marketing_copy: CopyContent
    image_prompts: list[str]
    final_brief: str
    model: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0

    def to_markdown(self) -> str:
        """Render a self-contained Markdown document for archival/sharing."""
        headlines = "\n".join(f"- {h}" for h in self.marketing_copy.headlines) or "- (none)"
        prompts = (
            "\n".join(f"{i}. {p}" for i, p in enumerate(self.image_prompts, start=1))
            or "_(none generated)_"
        )
        generated = self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"# Campaign Brief - {self.brief.product_name}\n\n"
            f"> Generated by Campaign Forge using `{self.model}` on {generated} "
            f"in {self.duration_seconds:.1f}s.\n\n"
            f"{self.final_brief.strip()}\n\n"
            "---\n\n"
            "## Appendix A - Research Summary\n\n"
            f"{self.research_summary.strip()}\n\n"
            "## Appendix B - Copy\n\n"
            f"**Tagline:** {self.marketing_copy.tagline}\n\n"
            f"**Primary message:** {self.marketing_copy.primary_message}\n\n"
            f"**Headlines:**\n{headlines}\n\n"
            f"**Body copy:**\n\n{self.marketing_copy.body_copy}\n\n"
            f"**Call to action:** {self.marketing_copy.call_to_action}\n\n"
            "## Appendix C - Image Prompts\n\n"
            f"{prompts}\n"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the result."""
        return self.model_dump(mode="json")

    def save(self, output_dir: Path | str) -> Path:
        """Persist the result as ``.md`` and ``.json`` files.

        The base filename is ``<timestamp>_<product-slug>``. Because the
        timestamp only has second resolution - and because non-Latin product
        names all slugify to the same fallback - two results can compete for the
        same name (routinely so in a concurrent batch). To avoid one silently
        overwriting another, the Markdown file is created *exclusively*; on a
        clash a numeric suffix (``-1``, ``-2``, ...) is appended until a free
        name is found.

        Args:
            output_dir: Directory to write into (created if necessary). A string
                is accepted and coerced to a :class:`~pathlib.Path`.

        Returns:
            Path to the written Markdown file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.generated_at.strftime("%Y%m%d-%H%M%S")
        slug = slugify(self.brief.product_name)
        markdown = self.to_markdown()

        attempt = 0
        while True:
            suffix = "" if attempt == 0 else f"-{attempt}"
            base = output_dir / f"{stamp}_{slug}{suffix}"
            md_path = base.with_suffix(".md")
            try:
                # Exclusive create ("x") is atomic: it fails rather than clobber
                # an existing file, so concurrent writers never lose data.
                with md_path.open("x", encoding="utf-8") as handle:
                    handle.write(markdown)
                break
            except FileExistsError:
                attempt += 1

        base.with_suffix(".json").write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return md_path
