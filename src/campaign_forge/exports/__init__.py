"""Run exports: Markdown, JSON and HTML downloads (Session 6).

* **Markdown:** the run's final report when it has one, otherwise every output
  rendered in turn, followed by the run's metadata.
* **JSON:** the complete run record (definition, inputs, steps, artifacts with
  their claims and evidence, usage). A run record never contains secrets: keys
  live only in settings.
* **HTML:** the Markdown rendered by ``markdown-it-py`` with **raw HTML
  disabled**, so model-written text cannot inject markup or scripts. Link
  targets are validated by markdown-it, which refuses ``javascript:`` and
  similar schemes. The page has no external resources.

Simulated runs (the offline demo responder) are labeled SIMULATED in every format.
"""

from __future__ import annotations

import html
import json
from typing import Any

from markdown_it import MarkdownIt

from ..agents.schemas import MODELS_BY_KIND, REPORT_KIND
from ..domain.artifacts import Artifact
from ..orchestration.state import RunRecord

SIMULATED_NOTICE = (
    "SIMULATED: this run used the offline demo responder, not a real model. The content is "
    "illustrative only."
)

_STYLE = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;max-width:52rem;"
    "margin:2rem auto;padding:0 1rem;line-height:1.55;color:#1f2328;background:#fff}"
    "h1,h2,h3{line-height:1.25}table{border-collapse:collapse;margin:1rem 0}"
    "th,td{border:1px solid #d0d7de;padding:.35rem .6rem;text-align:left;vertical-align:top}"
    "code,pre{background:#f6f8fa;border-radius:4px}pre{padding:.75rem;overflow-x:auto}"
    ".notice{border:1px solid #d4a72c;background:#fff8c5;padding:.6rem .9rem;border-radius:6px}"
    "footer{color:#59636e;font-size:.9rem;margin-top:2rem}"
)


def is_simulated(record: RunRecord) -> bool:
    """Whether any of the run's model calls were answered by the simulated provider."""
    return any(event.data.get("provider") == "fake" for event in record.events)


def artifact_markdown(artifact: Artifact) -> str:
    """Render any artifact as Markdown.

    In order: the contract's ``to_markdown()``; a ``markdown`` or ``text`` field (reports
    and v1 text payloads); the contract's ``digest()``; a bullet list; JSON.
    """
    model = MODELS_BY_KIND.get(artifact.kind)
    value: Any = artifact.as_model(model) if model is not None else None
    to_markdown = getattr(value, "to_markdown", None)
    if callable(to_markdown):
        return str(to_markdown())
    data = artifact.data
    for key in ("markdown", "text"):
        if isinstance(data.get(key), str):
            return str(data[key])
    digest = getattr(value, "digest", None)
    if callable(digest):
        return str(digest())
    if len(data) == 1:
        (only,) = data.values()
        if isinstance(only, list) and all(isinstance(item, str) for item in only):
            return "\n".join(f"- {item}" for item in only)
    return "```json\n" + json.dumps(data, indent=2, ensure_ascii=True) + "\n```"


def _title(record: RunRecord) -> str:
    return f"{record.definition.name} - run {record.run_id}"


def _metadata(record: RunRecord) -> str:
    usage = record.usage
    tokens = f"{usage.total_tokens}" + ("" if usage.complete else " (partly unreported)")
    return (
        f"Workflow: {record.definition.id}@{record.definition.version} | Run: {record.run_id} | "
        f"Status: {record.status.value} | Model calls: {record.llm_calls} | Tokens: {tokens} | "
        f"Created: {record.created_at:%Y-%m-%d %H:%M} UTC"
    )


def run_markdown(record: RunRecord) -> str:
    """The run as one Markdown document."""
    outputs = record.outputs()
    report = next((a for a in outputs.values() if a.kind == REPORT_KIND), None)
    parts: list[str] = []
    if is_simulated(record):
        parts.append(f"> {SIMULATED_NOTICE}")
    if report is not None:
        parts.append(artifact_markdown(report).strip())
    else:
        final = outputs.get("final_brief")
        if final is not None:
            parts.append(artifact_markdown(final).strip())
        else:
            parts.append(f"# {_title(record)}")
            for name, artifact in outputs.items():
                title = name.replace("_", " ").title()
                parts.append(f"## {title}\n\n{artifact_markdown(artifact)}")
            if not outputs:
                parts.append("_This run produced no outputs._")
    parts.append(f"---\n\n{_metadata(record)}")
    return "\n\n".join(parts) + "\n"


def run_json(record: RunRecord) -> str:
    """The complete run record as JSON, with a top-level ``simulated`` flag."""
    payload = {"simulated": is_simulated(record), "run": record.model_dump(mode="json")}
    return json.dumps(payload, indent=2, ensure_ascii=True)


def markdown_to_html(markdown: str) -> str:
    """Render Markdown to an HTML fragment with raw HTML disabled (safe for model text)."""
    renderer = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    return str(renderer.render(markdown))


def run_html(record: RunRecord) -> str:
    """The run as a standalone HTML page (no scripts, no external resources)."""
    markdown = run_markdown(record)
    if is_simulated(record):
        # The notice gets its own styled box; drop the Markdown quote that carries it.
        markdown = markdown.replace(f"> {SIMULATED_NOTICE}\n\n", "", 1)
        notice = f'<p class="notice">{html.escape(SIMULATED_NOTICE)}</p>\n'
    else:
        notice = ""
    body = markdown_to_html(markdown)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(_title(record))}</title>\n<style>{_STYLE}</style>\n</head>\n"
        f"<body>\n{notice}{body}<footer>Exported by Campaign Forge.</footer>\n</body>\n</html>\n"
    )


__all__ = [
    "SIMULATED_NOTICE",
    "artifact_markdown",
    "is_simulated",
    "markdown_to_html",
    "run_html",
    "run_json",
    "run_markdown",
]
