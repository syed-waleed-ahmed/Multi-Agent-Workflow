"""Lenient parsing helpers for turning raw model text into structured data.

Even with JSON mode enabled, models occasionally wrap output in Markdown code
fences or add stray prose. These helpers recover the payload defensively so a
cosmetic formatting quirk never fails an otherwise-valid response.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Bullet glyph models sometimes emit; built with chr() so this source stays ASCII.
_BULLET = chr(0x2022)
_ENUM_PREFIX_RE = re.compile(rf"^\s*(?:[-*{_BULLET}]|\d+[.)])\s*")
_MARKDOWN_EMPHASIS = "*_`#"


def strip_code_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence (```json ... ```), if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_braced(text: str, open_char: str, close_char: str) -> str:
    start = text.find(open_char)
    end = text.rfind(close_char)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON {open_char}...{close_char} block found in model output.")
    return text[start : end + 1]


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating fences and surrounding text.

    Raises:
        ValueError: if no JSON object can be recovered.
    """
    candidate = strip_code_fences(text)
    try:
        data: Any = json.loads(candidate)
    except json.JSONDecodeError:
        data = json.loads(_extract_braced(candidate, "{", "}"))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object at the top level of the model output.")
    return data


def parse_prompt_lines(text: str, *, min_words: int = 4) -> list[str]:
    """Extract a list of prompt strings from a numbered/bulleted text block.

    Used as a fallback when a model ignores the request for JSON. Lines shorter
    than ``min_words`` words are treated as headings and skipped.
    """
    prompts: list[str] = []
    for raw_line in text.splitlines():
        cleaned = _ENUM_PREFIX_RE.sub("", raw_line).strip().strip(_MARKDOWN_EMPHASIS).strip()
        if len(cleaned.split()) >= min_words:
            prompts.append(cleaned)
    return prompts
