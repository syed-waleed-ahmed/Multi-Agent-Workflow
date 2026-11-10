"""Campaign history: completed runs, remembered as searchable memory documents.

A remembered campaign is a compact digest (the brief plus a summary of each
output), not a copy of the run. The run itself stays in the run store, and the
document's ``ref`` is the run id. Remembering the same run again replaces its
document. The memory store redacts secrets before anything is written.
"""

from __future__ import annotations

from ..agents.library.common import digest
from ..agents.schemas import BRIEF_KIND
from ..memory.base import MAX_DOCUMENT_CHARS, MemoryDocument, MemoryKind, MemoryStore
from ..models import CampaignBrief
from .state import RunRecord, RunStatus

HISTORY_KIND: MemoryKind = "campaign"
_MAX_OUTPUT_CHARS = 1500


def history_document(record: RunRecord) -> tuple[str, str, dict[str, str | int | None]]:
    """The title, text and metadata that represent a run in campaign history."""
    brief_artifact = record.inputs.get("brief")
    brief = (
        brief_artifact.as_model(CampaignBrief)
        if brief_artifact is not None and brief_artifact.kind == BRIEF_KIND
        else None
    )
    subject = brief.product_name if brief else record.run_id
    title = f"{subject} - {record.definition.name}"
    parts = [f"Brief:\n{brief.as_prompt_context()}"] if brief else []
    for name, artifact in record.outputs().items():
        parts.append(f"{name.replace('_', ' ').title()}:\n{digest(artifact)[:_MAX_OUTPUT_CHARS]}")
    text = "\n\n".join(parts) or f"Run {record.run_id} of {record.definition.id}."
    metadata: dict[str, str | int | None] = {
        "run_id": record.run_id,
        "workflow": record.definition.id,
        "status": record.status.value,
        "llm_calls": record.llm_calls,
        "total_tokens": record.usage.total_tokens,
    }
    return title, text[:MAX_DOCUMENT_CHARS], metadata


def remember_run(memory: MemoryStore, record: RunRecord) -> MemoryDocument | None:
    """Store a completed run in campaign history. Other statuses are not remembered."""
    if record.status is not RunStatus.COMPLETED:
        return None
    title, text, metadata = history_document(record)
    return memory.add_document(
        record.scope, HISTORY_KIND, title, text, metadata=dict(metadata), ref=record.run_id
    )
