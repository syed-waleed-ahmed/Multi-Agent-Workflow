"""View models for the Streamlit app: plain data, no Streamlit import, unit-tested.

Everything the pages display is computed here: the workflow graph (Graphviz
DOT), table rows, plan previews, health information and the studio map. The
pages only lay these out. Health information never includes secret values,
only whether a key is set.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .. import __version__
from ..config import SECRET_FIELDS, Settings
from ..core.budget import BudgetLimits
from ..core.registry import AgentRegistry
from ..domain.artifacts import Artifact
from ..domain.brand import BrandProfile
from ..domain.sources import MAX_SOURCE_CHARS, SourceBundle, SourceDocument
from ..exceptions import CampaignForgeError
from ..models import CampaignBrief
from ..orchestration.definition import WorkflowDefinition
from ..orchestration.state import RunRecord, StepState
from ..orchestration.templates import BUILTIN_TEMPLATES
from ..providers.base import ModelClass
from ..providers.router import ProviderRouter

#: Node fill colours by step status (readable on light and dark themes).
STATUS_COLORS: Mapping[str, str] = {
    "planned": "#e3f2fd",
    "disabled": "#eeeeee",
    "pending": "#eceff1",
    "running": "#90caf9",
    "succeeded": "#a5d6a7",
    "waiting_for_approval": "#ffe082",
    "skipped": "#e0e0e0",
    "failed": "#ef9a9a",
    "cancelled": "#bcaaa4",
}

#: An example brief, so a first-time visitor can run something immediately.
EXAMPLE_BRIEF = CampaignBrief(
    product_name="EcoSip Reusable Bottle",
    product_description=(
        "An insulated stainless-steel bottle that keeps drinks cold for 24 hours and hot for "
        "12. Leak-proof lid, lifetime warranty."
    ),
    target_audience="eco-conscious young professionals who hike at weekends",
    goal="drive summer sales",
    tone="fresh, energetic, practical",
    channels=["instagram", "tiktok", "email", "google ads"],
)


# --------------------------------------------------------------------------- #
# Studios (ADR-018): entry points that pre-select workflow templates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Studio:
    id: str
    title: str
    description: str
    templates: tuple[str, ...]
    #: Set when the studio's own agents are not built yet.
    planned: str | None = None


STUDIOS: tuple[Studio, ...] = (
    Studio(
        "research",
        "Research Studio",
        "Evidence-backed competitor and market research, every cited claim fact-checked.",
        ("competitor-report", "full-campaign"),
    ),
    Studio(
        "strategy",
        "Strategy Studio",
        "A brand profile and positioning, or a full plan from research to channel mix.",
        ("brand-kit", "full-campaign"),
    ),
    Studio(
        "content",
        "Content Studio",
        "Reviewed copy and channel content: ads, email sequences, or the full set.",
        ("ad-pack", "email-sequence", "full-campaign"),
    ),
    Studio(
        "seo",
        "SEO Studio",
        "Keyword research, content plans and on-page optimisation.",
        (),
        planned="The SEO agents arrive in Session 8 (agent library wave 2).",
    ),
    Studio(
        "creative",
        "Creative Studio",
        "Visual concepts and image prompts with a brand's visual style.",
        ("full-campaign", "brand-kit"),
    ),
    Studio(
        "social",
        "Social Studio",
        "A reviewed two-week social calendar built on the campaign's core copy.",
        ("social-calendar",),
    ),
    Studio(
        "analytics",
        "Analytics Studio",
        "Performance analysis, experiment design and growth recommendations.",
        (),
        planned="The analytics agents arrive in Session 8 (agent library wave 2).",
    ),
)
STUDIOS_BY_ID: Mapping[str, Studio] = {s.id: s for s in STUDIOS}


# --------------------------------------------------------------------------- #
# Workflow graph
# --------------------------------------------------------------------------- #
def _quote(text: str) -> str:
    """A DOT string literal: backslashes and quotes escaped, newlines as DOT's ``\\n``."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def dag_dot(definition: WorkflowDefinition, steps: Mapping[str, StepState] | None = None) -> str:
    """Graphviz DOT for a workflow: inputs as ellipses, steps coloured by status.

    Without ``steps`` (a plan preview) steps are "planned" or "disabled".
    """
    lines = [
        "digraph workflow {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];',
        '  edge [color="#78909c"];',
    ]
    referenced = {
        ref
        for s in definition.steps
        for ref in (*s.inputs.values(), *(c.source for c in s.when))
        if ref.startswith("input:")
    }
    bound_inputs = sorted(
        {ref.split(":", 1)[1] for ref in referenced}
        & (set(definition.inputs) | set(definition.optional_inputs))
    )
    for name in bound_inputs:
        optional = name in definition.optional_inputs
        label = f"{name}{' (optional)' if optional else ''}"
        style = "dashed" if optional else "solid"
        lines.append(
            f"  {_quote('input:' + name)} [label={_quote(label)}, shape=ellipse, "
            f'style={style}, fillcolor="#ffffff"];'
        )
    for step in definition.steps:
        if steps is not None and step.id in steps:
            status = steps[step.id].status.value
        else:
            status = "planned" if step.enabled else "disabled"
        extras = [
            name
            for name, present in (("review", step.review), ("optional", step.optional))
            if present
        ]
        label = "\n".join(
            [step.id, step.agent]
            + ([f"({', '.join(extras)})"] if extras else [])
            + [f"[{status.replace('_', ' ')}]"]
        )
        color = STATUS_COLORS.get(status, "#ffffff")
        lines.append(
            f"  {_quote('step:' + step.id)} [label={_quote(label)}, fillcolor={_quote(color)}];"
        )
    for step in definition.steps:
        data = set(step.inputs.values())
        sources = sorted(data | {f"step:{d}" for d in step.after})
        sources += sorted({c.source for c in step.when} - set(sources))
        for source in sources:
            if source.startswith("input:") and source.split(":", 1)[1] not in bound_inputs:
                continue
            only_condition = source not in data and any(c.source == source for c in step.when)
            if only_condition:
                attrs = ' [style=dashed, label="if"]'
            elif source.startswith("input:"):
                # Most steps read the brief: keep input edges light so step order stands out.
                attrs = ' [color="#cfd8dc"]'
            else:
                attrs = ""
            lines.append(f"  {_quote(source)} -> {_quote('step:' + step.id)}{attrs};")
    lines.append("}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def plan_rows(definition: WorkflowDefinition) -> list[dict[str, Any]]:
    """One row per step of a plan preview."""
    rows = []
    for step in definition.steps:
        conditions = "; ".join(
            f"{c.source}{'.' + c.path if c.path else ''} {c.op}"
            + ("" if c.value is None else f" {c.value!r}")
            for c in step.when
        )
        rows.append(
            {
                "step": step.id,
                "agent": step.agent,
                "runs": "yes" if step.enabled else "skipped by you",
                "optional": step.optional,
                "reviewed": f"by {step.review.critic} (>= {step.review.threshold:.2f})"
                if step.review
                else "",
                "needs approval": step.approval,
                "condition": conditions,
                "after": ", ".join(sorted(step.dependencies())),
            }
        )
    return rows


def step_rows(record: RunRecord) -> list[dict[str, Any]]:
    rows = []
    for step in record.definition.steps:
        state = record.steps[step.id]
        seconds = (
            (state.finished_at - state.started_at).total_seconds()
            if state.started_at and state.finished_at
            else None
        )
        rows.append(
            {
                "step": step.id,
                "agent": step.agent,
                "status": state.status.value,
                "tries": state.attempts,
                "review": None if state.review_score is None else round(state.review_score, 2),
                "seconds": None if seconds is None else round(seconds, 1),
                "detail": state.error or state.detail or "",
            }
        )
    return rows


def run_rows(records: Iterable[RunRecord]) -> list[dict[str, Any]]:
    return [
        {
            "run": r.run_id,
            "workflow": r.definition.id,
            "status": r.status.value,
            "calls": r.llm_calls,
            "tokens": r.usage.total_tokens,
            "updated": r.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for r in records
    ]


def usage_summary(record: RunRecord) -> dict[str, Any]:
    usage = record.usage
    done = sum(1 for s in record.steps.values() if s.terminal)
    return {
        "model calls": record.llm_calls,
        "prompt tokens": usage.prompt_tokens,
        "completion tokens": usage.completion_tokens,
        "total tokens": usage.total_tokens,
        "usage complete": usage.complete,
        "steps finished": f"{done}/{len(record.steps)}",
    }


def claim_rows(artifact: Artifact) -> list[dict[str, Any]]:
    return [
        {
            "statement": c.statement,
            "basis": c.basis.value,
            "evidence": ", ".join(c.evidence_ids),
        }
        for c in artifact.claims
    ]


def evidence_rows(artifact: Artifact) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "title": e.title or "",
            "source": e.source,
            "retrieved": e.retrieved_at.strftime("%Y-%m-%d %H:%M"),
            "excerpt": " ".join(e.excerpt.split())[:300],
        }
        for e in artifact.evidence
    ]


def provenance(artifact: Artifact) -> dict[str, Any]:
    by = artifact.produced_by
    return {
        "agent": by.agent_id,
        "agent version": by.agent_version,
        "prompt version": by.prompt_version,
        "provider": by.provider or "",
        "model": by.model or "",
        "fallback from": by.fallback_from or "",
        "tokens": artifact.usage.total_tokens,
    }


def agent_rows(registry: AgentRegistry) -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "category": spec.category.value,
            "inputs": ", ".join(i.name if i.required else f"[{i.name}]" for i in spec.inputs),
            "output": spec.output_kind,
            "model class": spec.model_class.value,
            "tools": ", ".join(spec.allowed_tools),
            "enabled": registry.is_enabled(spec.id),
        }
        for spec in registry.specs()
    ]


# --------------------------------------------------------------------------- #
# Plans and inputs
# --------------------------------------------------------------------------- #
def template_ids(studio: Studio | None = None) -> list[str]:
    if studio is not None:
        return [t for t in studio.templates if t in BUILTIN_TEMPLATES]
    return list(BUILTIN_TEMPLATES)


def customize(
    definition: WorkflowDefinition, *, skip: Iterable[str] = (), limits: BudgetLimits | None = None
) -> WorkflowDefinition:
    """The definition with some optional steps disabled and, optionally, new limits.

    Raises:
        CampaignForgeError: if a step to skip is not optional.
    """
    skipped = set(skip)
    optional = {s.id for s in definition.steps if s.optional}
    refused = sorted(skipped - optional)
    if refused:
        raise CampaignForgeError(f"Only optional steps can be skipped: {', '.join(refused)}.")
    customised = definition.with_disabled(skipped) if skipped else definition
    if limits is not None:
        customised = customised.model_copy(update={"limits": limits})
    return customised


def errors_of(exc: ValidationError) -> list[str]:
    """Readable messages from a validation error (field: message)."""
    return [f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()]


def parse_brief_json(raw: bytes | str) -> CampaignBrief:
    """One brief from uploaded JSON (an object, or a list with exactly one object).

    Raises:
        CampaignForgeError: for invalid JSON or more than one brief.
        ValidationError: for an invalid brief.
    """
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CampaignForgeError(f"Not valid JSON: {exc}") from exc
    if isinstance(data, list):
        if len(data) != 1:
            raise CampaignForgeError(f"The file holds {len(data)} briefs; upload exactly one.")
        data = data[0]
    return CampaignBrief.model_validate(data)


def parse_brand_json(raw: bytes | str) -> BrandProfile:
    """A brand profile from uploaded JSON.

    Raises:
        CampaignForgeError: for invalid JSON.
        ValidationError: for an invalid profile.
    """
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CampaignForgeError(f"Not valid JSON: {exc}") from exc
    return BrandProfile.model_validate(data)


def text_sources(items: Sequence[tuple[str, str]]) -> SourceBundle | None:
    """User-supplied text (pasted notes, uploaded files) as a source bundle, capped in size."""
    documents = [
        SourceDocument(name=name[:300] or "notes", content=text.strip()[:MAX_SOURCE_CHARS])
        for name, text in items
        if text.strip()
    ]
    return SourceBundle(documents=tuple(documents)) if documents else None


def split_terms(text: str) -> list[str]:
    """Comma- or newline-separated terms, trimmed and without blanks."""
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


# --------------------------------------------------------------------------- #
# Settings and health
# --------------------------------------------------------------------------- #
def health_rows(settings: Settings, router: ProviderRouter, *, mode: str) -> list[dict[str, str]]:
    """Configuration facts for display. Keys appear only as "set" or "not set"."""
    rows = [
        {"setting": "Campaign Forge version", "value": __version__},
        {"setting": "App mode", "value": mode},
        {"setting": "Provider", "value": router.global_provider},
        {"setting": "FREE_ONLY", "value": "on" if settings.free_only else "off"},
    ]
    for model_class in ModelClass:
        try:
            route = router.resolve(model_class=model_class)
            value = f"{route.provider} / {route.model}"
        except CampaignForgeError as exc:
            value = f"not available: {exc}"
        rows.append({"setting": f"Model ({model_class.value})", "value": value})
    fallbacks = ", ".join(settings.fallback_provider_names) or "none"
    rows.append({"setting": "Fallback providers", "value": fallbacks})
    tools = ", ".join(settings.research_tool_names) or "none"
    rows.append({"setting": "Research tools", "value": tools})
    if mode == "demo":
        where = "this browser session only (nothing on the server)"
        rows.append({"setting": "Storage", "value": where})
    else:
        store = (
            f"SQLite ({settings.memory_db})"
            if settings.run_store == "sqlite"
            else f"JSON ({settings.runs_dir})"
        )
        rows.append({"setting": "Run store", "value": store})
        rows.append({"setting": "Memory database", "value": str(settings.memory_db)})
    for name in SECRET_FIELDS:
        rows.append(
            {
                "setting": name.upper(),
                "value": "set" if settings.secret(name) else "not set",
            }
        )
    return rows
