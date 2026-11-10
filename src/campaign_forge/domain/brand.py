"""Brand profiles (``brand.profile@1``): the voice and vocabulary a brand's content follows.

A profile is created by the ``brand_strategist`` agent or written by hand, stored
per tenant scope in memory, and passed to a workflow as its ``brand`` input.

Banned and preferred terms are checked by code (:func:`term_counts`), not by a
model, so a brand check is reproducible: the same text always gives the same hits.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

BRAND_KIND = "brand.profile@1"
MAX_TERMS = 25

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def brand_slug(name: str) -> str:
    """The storage key for a brand name: lower-case words joined by hyphens."""
    slug = _SLUG_RE.sub("-", name.casefold()).strip("-")[:60].strip("-")
    return slug or "brand-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive; any run of whitespace matches a space in the term."""
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def term_counts(text: str, terms: Iterable[str]) -> dict[str, int]:
    """How often each term occurs in ``text`` as a whole word or phrase."""
    return {term: len(_term_pattern(term).findall(text)) for term in terms if term.strip()}


class BrandProfile(BaseModel):
    """A practical brand voice guide: what writers follow, and what code checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    voice: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    tone_words: list[Term] = Field(default_factory=list, max_length=8)
    audience: str = Field(default="", max_length=400)
    values: list[Line] = Field(default_factory=list, max_length=8)
    #: Things the brand's content always does.
    guidelines: list[Line] = Field(default_factory=list, max_length=10)
    #: Things the brand's content never does.
    avoid: list[Line] = Field(default_factory=list, max_length=10)
    preferred_terms: list[Term] = Field(default_factory=list, max_length=MAX_TERMS)
    #: Words or phrases that must not appear in the brand's content (checked by code).
    banned_terms: list[Term] = Field(default_factory=list, max_length=MAX_TERMS)
    visual_style: str = Field(default="", max_length=600)

    @field_validator(
        "tone_words", "values", "guidelines", "avoid", "preferred_terms", "banned_terms"
    )
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return _dedupe(value)

    @model_validator(mode="after")
    def _no_conflicting_terms(self) -> BrandProfile:
        banned = {t.casefold() for t in self.banned_terms}
        clash = sorted(t for t in self.preferred_terms if t.casefold() in banned)
        if clash:
            raise ValueError(f"terms are both preferred and banned: {', '.join(clash)}")
        return self

    @property
    def slug(self) -> str:
        return brand_slug(self.name)

    def digest(self) -> str:
        lines = [f"Brand: {self.name}", f"Voice: {self.voice}"]
        if self.tone_words:
            lines.append(f"Tone words: {', '.join(self.tone_words)}")
        if self.audience:
            lines.append(f"Audience: {self.audience}")
        if self.values:
            lines.append(f"Values: {'; '.join(self.values)}")
        if self.guidelines:
            lines.append(f"Always: {'; '.join(self.guidelines)}")
        if self.avoid:
            lines.append(f"Never: {'; '.join(self.avoid)}")
        if self.preferred_terms:
            lines.append(f"Preferred terms: {', '.join(self.preferred_terms)}")
        if self.banned_terms:
            lines.append(f"Banned terms: {', '.join(self.banned_terms)}")
        if self.visual_style:
            lines.append(f"Visual style: {self.visual_style}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) or "- (none)"

        return (
            f"**Brand:** {self.name}\n\n**Voice:** {self.voice}\n\n"
            f"**Tone words:** {', '.join(self.tone_words) or '(none)'}\n\n"
            + (f"**Audience:** {self.audience}\n\n" if self.audience else "")
            + f"**Values:**\n{bullets(self.values)}\n\n"
            f"**Always:**\n{bullets(self.guidelines)}\n\n"
            f"**Never:**\n{bullets(self.avoid)}\n\n"
            f"**Preferred terms:** {', '.join(self.preferred_terms) or '(none)'}\n\n"
            f"**Banned terms:** {', '.join(self.banned_terms) or '(none)'}"
            + (f"\n\n**Visual style:** {self.visual_style}" if self.visual_style else "")
        )
