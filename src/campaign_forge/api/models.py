"""Typed request and response models for the HTTP API, and their mapping from service objects."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.agent import AgentSpec
from ..domain.artifacts import Artifact
from ..domain.brand import BrandProfile
from ..domain.sources import MAX_SOURCES, SourceBundle, SourceDocument
from ..exports import artifact_markdown, is_simulated
from ..models import CampaignBrief
from ..orchestration.state import RunRecord
from ..services import CatalogEntry, Execution, ProviderInfo, RunOptions
from ..services.catalog import DEFAULT_CAMPAIGN

API_PREFIX = "/api/v1"


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    """Inputs and plan options for a run. Limits can only lower the workflow's own."""

    model_config = ConfigDict(extra="forbid")

    brief: CampaignBrief
    brand: BrandProfile | None = Field(
        default=None, description="A brand profile the copy must follow (banned terms enforced)."
    )
    sources: list[SourceDocument] = Field(
        default_factory=list,
        max_length=MAX_SOURCES,
        description="Your own source material; research agents cite it as user-provided.",
    )
    skip_steps: list[str] = Field(
        default_factory=list, max_length=50, description="Optional steps to skip."
    )
    max_llm_calls: int | None = Field(default=None, ge=1, le=1000)
    max_total_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    max_wall_seconds: float | None = Field(default=None, gt=0, le=86_400)

    def inputs(self) -> dict[str, BaseModel]:
        values: dict[str, BaseModel] = {"brief": self.brief}
        if self.brand is not None:
            values["brand"] = self.brand
        if self.sources:
            values["sources"] = SourceBundle(documents=tuple(self.sources))
        return values

    def options(self) -> RunOptions:
        return RunOptions(
            skip_steps=tuple(dict.fromkeys(s.strip() for s in self.skip_steps if s.strip())),
            max_llm_calls=self.max_llm_calls,
            max_total_tokens=self.max_total_tokens,
            max_wall_seconds=self.max_wall_seconds,
        )


class CampaignRequest(RunRequest):
    workflow: str = Field(
        default=DEFAULT_CAMPAIGN, description="A built-in workflow, e.g. full-campaign or ad-pack."
    )


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=60)
    approved: bool = True
    comment: str = Field(default="", max_length=2000)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class ErrorDetail(BaseModel):
    type: str
    message: str
    details: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class AgentInputView(BaseModel):
    name: str
    kind: str
    required: bool


class AgentView(BaseModel):
    id: str
    name: str
    description: str
    category: str
    version: str
    inputs: list[AgentInputView]
    output_kind: str
    model_class: str
    capabilities: list[str]
    allowed_tools: list[str]
    enabled: bool


class AgentDetail(AgentView):
    prompt_version: str
    rubric: list[str]
    limits: dict[str, Any]


class WorkflowView(BaseModel):
    id: str
    name: str
    version: str
    description: str
    source: Literal["builtin", "custom"]
    steps: int
    inputs: dict[str, str]
    optional_inputs: dict[str, str]


class WorkflowDetail(WorkflowView):
    definition: dict[str, Any]


class StepView(BaseModel):
    id: str
    agent: str
    status: str
    attempts: int
    review_score: float | None
    skip_reason: str | None
    detail: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class UsageView(BaseModel):
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    complete: bool


class ArtifactView(BaseModel):
    kind: str
    markdown: str
    data: dict[str, Any]
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]


class ExecutionSummary(BaseModel):
    id: str
    workflow: str
    status: str
    created_at: datetime
    updated_at: datetime
    llm_calls: int
    total_tokens: int


class ExecutionView(BaseModel):
    id: str
    workflow: str | None
    status: str = Field(
        description="'starting' until the run's first checkpoint is saved ('failed' if it "
        "stopped before that)."
    )
    status_reason: str | None
    resume_after: float | None
    running: bool
    error: str | None
    simulated: bool
    created_at: datetime | None
    updated_at: datetime | None
    usage: UsageView | None
    steps: list[StepView]
    outputs: dict[str, ArtifactView]
    links: dict[str, str]


class CampaignView(ExecutionView):
    brief: CampaignBrief | None
    report_markdown: str | None


class ProviderView(BaseModel):
    name: str
    display_name: str
    free_tier: bool
    configured: bool
    allowed: bool
    is_global: bool
    default_model: str | None
    models: dict[str, str]
    data_use_notice: str


class SystemStatus(BaseModel):
    version: str
    simulated: bool
    provider: str
    free_only: bool
    run_store: str
    research_tools: list[str]
    active_runs: int
    max_active_runs: int
    auth: Literal["api_key"]
    keys_configured: int
    rate_limit_per_minute: int
    run_limit_per_minute: int


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
def agent_view(spec: AgentSpec, *, enabled: bool) -> AgentView:
    return AgentView(
        id=spec.id,
        name=spec.name,
        description=spec.description,
        category=spec.category.value,
        version=spec.version,
        inputs=[AgentInputView(name=i.name, kind=i.kind, required=i.required) for i in spec.inputs],
        output_kind=spec.output_kind,
        model_class=spec.model_class.value,
        capabilities=list(spec.capabilities),
        allowed_tools=list(spec.allowed_tools),
        enabled=enabled,
    )


def agent_detail(spec: AgentSpec, *, enabled: bool) -> AgentDetail:
    return AgentDetail(
        **agent_view(spec, enabled=enabled).model_dump(),
        prompt_version=spec.prompt_version,
        rubric=list(spec.rubric),
        limits=spec.limits.model_dump(),
    )


def workflow_view(entry: CatalogEntry) -> WorkflowView:
    d = entry.definition
    return WorkflowView(
        id=d.id,
        name=d.name,
        version=d.version,
        description=d.description,
        source=entry.source,
        steps=len(d.steps),
        inputs=dict(d.inputs),
        optional_inputs=dict(d.optional_inputs),
    )


def workflow_detail(entry: CatalogEntry) -> WorkflowDetail:
    return WorkflowDetail(
        **workflow_view(entry).model_dump(), definition=entry.definition.model_dump(mode="json")
    )


def _artifact_view(artifact: Artifact) -> ArtifactView:
    return ArtifactView(
        kind=artifact.kind,
        markdown=artifact_markdown(artifact),
        data=artifact.data,
        claims=[c.model_dump(mode="json") for c in artifact.claims],
        evidence=[e.model_dump(mode="json") for e in artifact.evidence],
    )


def _steps(record: RunRecord) -> list[StepView]:
    views = []
    for step in record.definition.steps:
        state = record.steps[step.id]
        views.append(
            StepView(
                id=step.id,
                agent=step.agent,
                status=state.status.value,
                attempts=state.attempts,
                review_score=state.review_score,
                skip_reason=state.skip_reason.value if state.skip_reason else None,
                detail=state.detail,
                error=state.error,
                started_at=state.started_at,
                finished_at=state.finished_at,
            )
        )
    return views


def execution_view(execution: Execution) -> ExecutionView:
    record = execution.record
    links = {"self": f"{API_PREFIX}/executions/{execution.run_id}"}
    if record is None:
        return ExecutionView(
            id=execution.run_id,
            workflow=None,
            # No checkpoint yet: still starting, or it failed before its first save.
            status="failed" if execution.error and not execution.running else "starting",
            status_reason=None,
            resume_after=None,
            running=execution.running,
            error=execution.error,
            simulated=False,
            created_at=None,
            updated_at=None,
            usage=None,
            steps=[],
            outputs={},
            links=links,
        )
    usage = record.usage
    return ExecutionView(
        id=record.run_id,
        workflow=f"{record.definition.id}@{record.definition.version}",
        status=record.status.value,
        status_reason=record.status_reason,
        resume_after=record.resume_after,
        running=execution.running,
        error=execution.error,
        simulated=is_simulated(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
        usage=UsageView(
            llm_calls=record.llm_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            complete=usage.complete,
        ),
        steps=_steps(record),
        outputs={name: _artifact_view(a) for name, a in record.outputs().items()},
        links=links,
    )


def campaign_view(execution: Execution) -> CampaignView:
    base = execution_view(execution).model_dump()
    base["links"] = {**base["links"], "campaign": f"{API_PREFIX}/campaigns/{execution.run_id}"}
    record = execution.record
    brief = None
    report = None
    if record is not None:
        brief_artifact = record.inputs.get("brief")
        if brief_artifact is not None:
            brief = brief_artifact.as_model(CampaignBrief)
        report = next(
            (v["markdown"] for v in base["outputs"].values() if v["kind"].startswith("report.")),
            None,
        )
    return CampaignView(**base, brief=brief, report_markdown=report)


def execution_summary(record: RunRecord) -> ExecutionSummary:
    return ExecutionSummary(
        id=record.run_id,
        workflow=f"{record.definition.id}@{record.definition.version}",
        status=record.status.value,
        created_at=record.created_at,
        updated_at=record.updated_at,
        llm_calls=record.llm_calls,
        total_tokens=record.usage.total_tokens,
    )


def provider_view(info: ProviderInfo) -> ProviderView:
    return ProviderView(
        name=info.name,
        display_name=info.display_name,
        free_tier=info.free_tier,
        configured=info.configured,
        allowed=info.allowed,
        is_global=info.is_global,
        default_model=info.default_model,
        models=dict(info.models),
        data_use_notice=info.data_use_notice,
    )
