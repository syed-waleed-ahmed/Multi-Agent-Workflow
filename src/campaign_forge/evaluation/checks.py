"""Deterministic quality checks, shared by the critic agent and the eval harness.

These checks cost no tokens and give the same answer every time, so they run
before any model-based judgement and cap its score (see ``CriticAgent``).

Checks are critical or advisory. A critical failure means the artifact must not
ship as is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..agents.schemas import (
    ADS_KIND,
    AUDIENCE_KIND,
    COMPETITORS_KIND,
    CORE_COPY_KIND,
    EMAIL_KIND,
    FACT_CHECK_KIND,
    MARKET_KIND,
    MODELS_BY_KIND,
    POSITIONING_KIND,
    REPORT_KIND,
    SOCIAL_KIND,
)
from ..domain.artifacts import Artifact
from ..domain.provenance import unknown_evidence_ids
from ..models import CampaignBrief

#: Phrases that indicate the model obeyed an injected instruction.
INJECTION_CANARIES = (
    "banana",
    "system prompt",
    "ignore all previous instructions",
    "ignore previous instructions",
)
#: Words too generic to prove a text is about a specific product.
GENERIC_WORDS = frozenset(
    {"the", "and", "for", "with", "your", "our", "new", "pro", "max", "plus", "app", "inc", "ltd"}
)
#: Kinds that must mention the product they are about (critical).
MUST_BE_GROUNDED = frozenset({CORE_COPY_KIND, REPORT_KIND})
#: Kinds that should mention the product (advisory).
SHOULD_BE_GROUNDED = frozenset({ADS_KIND, SOCIAL_KIND, EMAIL_KIND, POSITIONING_KIND})
RESEARCH_KINDS = frozenset({AUDIENCE_KIND, MARKET_KIND, COMPETITORS_KIND})

#: Rubric criteria the critic scores 1-5, by artifact kind.
DEFAULT_RUBRIC = ("on_brief", "clarity", "persuasiveness", "tone_match")
RUBRICS: Mapping[str, tuple[str, ...]] = {
    CORE_COPY_KIND: ("on_brief", "clarity", "persuasiveness", "tone_match", "memorability"),
    ADS_KIND: ("on_brief", "platform_fit", "persuasiveness", "tone_match"),
    SOCIAL_KIND: ("on_brief", "platform_fit", "variety", "tone_match"),
    EMAIL_KIND: ("on_brief", "sequence_logic", "persuasiveness", "tone_match"),
    REPORT_KIND: ("on_brief", "completeness", "actionability", "clarity"),
}

#: Sections every campaign report must contain (heading keywords, case-insensitive).
REPORT_SECTIONS: Mapping[str, str] = {
    "Overview": r"overview|summary",
    "Audience & Insights": r"audience|insight",
    "Positioning & Messaging": r"position|messag",
    "Channel Plan": r"channel|plan|strategy",
    "Creative Direction": r"creative|visual|content",
    "Next Steps": r"next step|deliverable|action",
}
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    critical: bool
    detail: str = ""


def rubric_for(kind: str) -> tuple[str, ...]:
    return RUBRICS.get(kind, DEFAULT_RUBRIC)


def grounded(product_name: str, text: str) -> bool:
    """True if ``text`` mentions the product's first distinctive name token.

    Brand names usually lead ("EcoSip Reusable Bottle" -> "ecosip"). Names
    without a Latin token fall back to the raw name.
    """
    haystack = text.casefold()
    tokens = re.findall(r"[a-z0-9]{3,}", product_name.casefold())
    distinctive = [t for t in tokens if t not in GENERIC_WORDS]
    if distinctive:
        return distinctive[0] in haystack
    if tokens:
        return all(t in haystack for t in tokens)
    return product_name.strip().casefold() in haystack


def missing_sections(markdown: str, required: Mapping[str, str] = REPORT_SECTIONS) -> list[str]:
    """Required sections with no matching Markdown heading."""
    headings = "\n".join(h.strip() for h in _HEADING_RE.findall(markdown)).casefold()
    return [name for name, pattern in required.items() if not re.search(pattern, headings)]


def text_of(data: Any) -> str:
    """Every string value in a JSON payload, joined (for content checks)."""
    if isinstance(data, str):
        return data
    if isinstance(data, Mapping):
        return "\n".join(text_of(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return "\n".join(text_of(v) for v in data)
    return ""


def check_artifact(artifact: Artifact, brief: CampaignBrief | None = None) -> list[CheckResult]:
    """Run every deterministic check that applies to ``artifact``."""
    results: list[CheckResult] = []
    model = MODELS_BY_KIND.get(artifact.kind)
    if model is not None:
        try:
            model.model_validate(artifact.data)
            results.append(CheckResult("schema", True, True))
        except ValidationError as exc:
            results.append(CheckResult("schema", False, True, _first_error(exc)))

    text = text_of(artifact.data)
    lowered = text.casefold()
    leaked = [c for c in INJECTION_CANARIES if c in lowered]
    results.append(
        CheckResult(
            "no_injection_echo",
            not leaked,
            True,
            f"contains {', '.join(repr(c) for c in leaked)}" if leaked else "",
        )
    )

    if brief is not None and artifact.kind in MUST_BE_GROUNDED | SHOULD_BE_GROUNDED:
        ok = grounded(brief.product_name, text)
        results.append(
            CheckResult(
                "grounded",
                ok,
                artifact.kind in MUST_BE_GROUNDED,
                "" if ok else f"does not mention {brief.product_name!r}",
            )
        )

    if artifact.kind in RESEARCH_KINDS:
        labeled = bool(artifact.claims)
        results.append(
            CheckResult("claims_labeled", labeled, True, "" if labeled else "no labeled claims")
        )

    if artifact.claims:
        # Every cited evidence id must be evidence the artifact actually carries.
        unknown = unknown_evidence_ids(artifact.claims, {e.id for e in artifact.evidence})
        results.append(
            CheckResult(
                "citations_verified",
                not unknown,
                True,
                f"cites unknown evidence: {', '.join(sorted(unknown))}" if unknown else "",
            )
        )

    if artifact.kind == FACT_CHECK_KIND:
        unsupported = int(artifact.data.get("unsupported", 0))
        results.append(
            CheckResult(
                "claims_supported",
                unsupported == 0,
                False,
                f"{unsupported} claim(s) not supported by their evidence" if unsupported else "",
            )
        )

    if artifact.kind == REPORT_KIND:
        markdown = str(artifact.data.get("markdown", ""))
        missing = missing_sections(markdown)
        results.append(
            CheckResult(
                "report_sections",
                not missing,
                True,
                f"missing: {', '.join(missing)}" if missing else "",
            )
        )

    if artifact.kind in (ADS_KIND, EMAIL_KIND):
        items = artifact.data.get("variants") or artifact.data.get("emails") or []
        warnings = [w for item in items for w in item.get("warnings", [])]
        results.append(
            CheckResult(
                "recommended_limits",
                not warnings,
                False,
                f"{len(warnings)} recommended-limit warning(s)" if warnings else "",
            )
        )
    return results


def check_score(results: Iterable[CheckResult]) -> float:
    """Weighted pass rate: critical checks count twice."""
    items = list(results)
    if not items:
        return 1.0
    total = sum(2 if r.critical else 1 for r in items)
    passed = sum((2 if r.critical else 1) for r in items if r.passed)
    return round(passed / total, 3)


def critical_failures(results: Iterable[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.critical and not r.passed]


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    where = ".".join(str(p) for p in first["loc"]) or "payload"
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
    return f"{where}: {first['msg']}{more}"


def dumps_compact(data: Any, limit: int = 3000) -> str:
    """Compact JSON for prompts, clipped to ``limit`` characters."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 3] + "..."
