"""The HTTP API (``[api]`` extra): a thin mapping over :class:`~campaign_forge.services.Platform`.

Run it with ``uvicorn campaign_forge.api.app:create_app --factory`` or
``python -m campaign_forge.api``. See docs/API.md and the OpenAPI schema in
docs/openapi.json.

* **Authentication.** Every ``/api/v1`` endpoint needs an ``X-API-Key`` header
  matching one of ``CF_API_KEYS``. With no keys configured, they answer 401: the
  API is closed by default. ``/health`` is open.
* **Tenancy.** Each key is its own scope, so clients never see each other's runs
  or workflows.
* **Limits.** Requests per minute and run starts per minute per key, plus a cap
  on background runs executing at once. A limited request gets 429 (503 when the
  server is at capacity) with ``Retry-After``.
* **Errors.** One JSON shape: ``{"error": {"type", "message", "details"}}``.
  Messages are redacted, and unexpected errors return a generic 500.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Settings
from ..domain.scope import Scope
from ..exceptions import (
    CampaignForgeError,
    CapacityError,
    ConfigurationError,
    NotFoundError,
    ProviderNotAllowedError,
    RunStateError,
    WorkflowValidationError,
)
from ..logging_config import get_logger
from ..orchestration.definition import WorkflowDefinition
from ..security.limits import SlidingWindowLimiter
from ..security.redaction import redact
from ..services import Platform
from .models import (
    API_PREFIX,
    AgentDetail,
    AgentView,
    ApprovalRequest,
    CampaignRequest,
    CampaignView,
    ErrorResponse,
    ExecutionSummary,
    ExecutionView,
    Health,
    ProviderView,
    RunRequest,
    SystemStatus,
    WorkflowDetail,
    WorkflowView,
    agent_detail,
    agent_view,
    campaign_view,
    execution_summary,
    execution_view,
    provider_view,
    workflow_detail,
    workflow_view,
)

API_KEY_HEADER = "X-API-Key"
_CAPACITY_RETRY_S = 30
_ERROR_DESCRIPTIONS = {
    400: "Bad request",
    401: "Missing or invalid API key",
    404: "Not found (including resources owned by another key)",
    409: "Conflict with the run's state",
    422: "Invalid request or workflow definition",
    429: "Rate limited",
    503: "At capacity",
}

_log = get_logger("api")


class ApiError(Exception):
    """An error with an HTTP status, rendered in the API's error shape."""

    def __init__(
        self,
        status_code: int,
        error_type: str,
        message: str,
        *,
        details: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.details = details or []
        self.headers = headers or {}


def _scope_for(key: str) -> Scope:
    """A stable, non-reversible tenant id for an API key."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Scope(org_id="api", user_id=f"key-{digest}")


def _status_for(exc: CampaignForgeError) -> tuple[int, str]:
    if isinstance(exc, NotFoundError):
        return status.HTTP_404_NOT_FOUND, "not_found"
    if isinstance(exc, WorkflowValidationError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_workflow"
    if isinstance(exc, ProviderNotAllowedError):
        return status.HTTP_403_FORBIDDEN, "provider_not_allowed"
    if isinstance(exc, ConfigurationError):
        return status.HTTP_400_BAD_REQUEST, "bad_request"
    if isinstance(exc, RunStateError):
        return status.HTTP_409_CONFLICT, "conflict"
    if isinstance(exc, CapacityError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "at_capacity"
    return status.HTTP_400_BAD_REQUEST, "error"


def create_app(settings: Settings | None = None, *, platform: Platform | None = None) -> FastAPI:
    """Build the API. Tests pass their own ``platform``; production builds one from settings."""
    settings = settings or Settings()
    platform = platform or Platform(settings)
    secrets = settings.secret_values()
    scopes = {key: _scope_for(key) for key in settings.api_key_values}
    requests = SlidingWindowLimiter(settings.api_rate_limit)
    run_starts = SlidingWindowLimiter(settings.api_run_limit)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not scopes:
            _log.warning("CF_API_KEYS is not set: every /api/v1 endpoint will answer 401.")
        yield
        platform.close()

    app = FastAPI(
        title="Campaign Forge API",
        version=__version__,
        description=(
            "Multi-agent marketing workflows over HTTP. Send `X-API-Key` on every `/api/v1` "
            "request. Runs execute in the background: start one, then poll its execution."
        ),
        lifespan=lifespan,
        redoc_url=None,
    )
    app.state.platform = platform

    def render(exc: ApiError) -> JSONResponse:
        body = ErrorResponse.model_validate(
            {
                "error": {
                    "type": exc.error_type,
                    "message": redact(exc.message, secrets),
                    "details": [redact(d, secrets) for d in exc.details],
                }
            }
        )
        return JSONResponse(body.model_dump(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return render(exc)

    @app.exception_handler(CampaignForgeError)
    async def _domain_error(request: Request, exc: CampaignForgeError) -> JSONResponse:
        code, kind = _status_for(exc)
        details = list(exc.problems) if isinstance(exc, WorkflowValidationError) else []
        headers = {"Retry-After": str(_CAPACITY_RETRY_S)} if isinstance(exc, CapacityError) else {}
        return render(ApiError(code, kind, str(exc), details=details, headers=headers))

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', 'invalid')}"
            for e in exc.errors()
        ]
        invalid = ApiError(422, "invalid_request", "The request is not valid.", details=details)
        return render(invalid)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        kind = "not_found" if exc.status_code == 404 else "http_error"
        return render(ApiError(exc.status_code, kind, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        _log.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return render(ApiError(500, "internal_error", "Internal server error."))

    api_key = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

    def caller(key: str | None = Security(api_key)) -> Scope:
        if not scopes:
            raise ApiError(
                401, "unauthorized", "API keys are not configured on this server (CF_API_KEYS)."
            )
        matched: Scope | None = None
        for known, scope in scopes.items():  # compare against every key: constant time
            if key is not None and hmac.compare_digest(key.encode(), known.encode()):
                matched = scope
        if matched is None:
            raise ApiError(
                401,
                "unauthorized",
                f"Missing or invalid {API_KEY_HEADER} header.",
                headers={"WWW-Authenticate": API_KEY_HEADER},
            )
        _limit(requests, matched, "requests")
        return matched

    def run_starter(scope: Scope = Depends(caller)) -> Scope:
        _limit(run_starts, scope, "run starts")
        return scope

    @app.get("/health", response_model=Health, tags=["system"])
    def health() -> Health:
        """Liveness: the process is up. Needs no key."""
        return Health(version=__version__)

    # Explicit descriptions keep the schema identical on every Python version (3.13
    # renamed the 422 reason phrase, which FastAPI would otherwise copy).
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse, "description": description}
        for code, description in _ERROR_DESCRIPTIONS.items()
    }
    v1 = APIRouter(prefix=API_PREFIX, responses=errors)

    @v1.get("/agents", response_model=list[AgentView], tags=["agents"])
    def list_agents(scope: Scope = Depends(caller)) -> list[AgentView]:
        return [
            agent_view(spec, enabled=platform.agent_enabled(spec.id)) for spec in platform.agents()
        ]

    @v1.get("/agents/{agent_id}", response_model=AgentDetail, tags=["agents"])
    def get_agent(agent_id: str, scope: Scope = Depends(caller)) -> AgentDetail:
        spec = platform.agent(agent_id)
        return agent_detail(spec, enabled=platform.agent_enabled(spec.id))

    @v1.post(
        "/campaigns",
        response_model=CampaignView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["campaigns"],
    )
    def create_campaign(
        body: CampaignRequest, response: Response, scope: Scope = Depends(run_starter)
    ) -> CampaignView:
        """Start a campaign (a built-in workflow on a brief). Poll the returned execution."""
        run_id = platform.start_campaign(scope, body.workflow, body.inputs(), body.options())
        response.headers["Location"] = f"{API_PREFIX}/campaigns/{run_id}"
        return campaign_view(platform.execution(scope, run_id))

    @v1.get("/campaigns/{campaign_id}", response_model=CampaignView, tags=["campaigns"])
    def get_campaign(campaign_id: str, scope: Scope = Depends(caller)) -> CampaignView:
        return campaign_view(platform.execution(scope, campaign_id))

    @v1.get("/workflows", response_model=list[WorkflowView], tags=["workflows"])
    def list_workflows(scope: Scope = Depends(caller)) -> list[WorkflowView]:
        return [workflow_view(entry) for entry in platform.list_workflows(scope)]

    @v1.post(
        "/workflows",
        response_model=WorkflowDetail,
        status_code=status.HTTP_201_CREATED,
        tags=["workflows"],
    )
    def create_workflow(
        definition: WorkflowDefinition, response: Response, scope: Scope = Depends(caller)
    ) -> WorkflowDetail:
        """Store a custom workflow for this key, validated against the agent registry."""
        entry = platform.save_workflow(scope, definition)
        response.headers["Location"] = f"{API_PREFIX}/workflows/{definition.id}"
        return workflow_detail(entry)

    @v1.get("/workflows/{workflow_id}", response_model=WorkflowDetail, tags=["workflows"])
    def get_workflow(workflow_id: str, scope: Scope = Depends(caller)) -> WorkflowDetail:
        return workflow_detail(platform.workflow(scope, workflow_id))

    @v1.post(
        "/workflows/{workflow_id}/run",
        response_model=ExecutionView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["workflows"],
    )
    def run_workflow(
        workflow_id: str,
        body: RunRequest,
        response: Response,
        scope: Scope = Depends(run_starter),
    ) -> ExecutionView:
        run_id = platform.run_workflow(scope, workflow_id, body.inputs(), body.options())
        response.headers["Location"] = f"{API_PREFIX}/executions/{run_id}"
        return execution_view(platform.execution(scope, run_id))

    @v1.get("/executions", response_model=list[ExecutionSummary], tags=["executions"])
    def list_executions(scope: Scope = Depends(caller)) -> list[ExecutionSummary]:
        return [execution_summary(record) for record in platform.executions(scope)]

    @v1.get("/executions/{execution_id}", response_model=ExecutionView, tags=["executions"])
    def get_execution(execution_id: str, scope: Scope = Depends(caller)) -> ExecutionView:
        return execution_view(platform.execution(scope, execution_id))

    @v1.post(
        "/executions/{execution_id}/resume",
        response_model=ExecutionView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["executions"],
    )
    def resume_execution(execution_id: str, scope: Scope = Depends(run_starter)) -> ExecutionView:
        """Continue a paused, failed or interrupted run from its checkpoint."""
        platform.resume(scope, execution_id)
        return execution_view(platform.execution(scope, execution_id))

    @v1.post("/executions/{execution_id}/cancel", response_model=ExecutionView, tags=["executions"])
    def cancel_execution(execution_id: str, scope: Scope = Depends(caller)) -> ExecutionView:
        platform.cancel(scope, execution_id)
        return execution_view(platform.execution(scope, execution_id))

    @v1.post(
        "/executions/{execution_id}/approve",
        response_model=ExecutionView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["executions"],
    )
    def approve_step(
        execution_id: str, body: ApprovalRequest, scope: Scope = Depends(run_starter)
    ) -> ExecutionView:
        """Approve (or reject, which cancels the run) a step waiting for a human."""
        platform.approve(
            scope, execution_id, body.step_id, approved=body.approved, comment=body.comment
        )
        return execution_view(platform.execution(scope, execution_id))

    @v1.get("/providers", response_model=list[ProviderView], tags=["system"])
    def list_providers(scope: Scope = Depends(caller)) -> list[ProviderView]:
        """Supported providers. Keys appear only as configured or not."""
        return [provider_view(info) for info in platform.providers()]

    @v1.get("/system/status", response_model=SystemStatus, tags=["system"])
    def system_status(scope: Scope = Depends(caller)) -> SystemStatus:
        return SystemStatus(
            version=__version__,
            auth="api_key",
            keys_configured=len(scopes),
            rate_limit_per_minute=settings.api_rate_limit,
            run_limit_per_minute=settings.api_run_limit,
            **platform.status(),
        )

    app.include_router(v1)
    return app


def _limit(limiter: SlidingWindowLimiter, scope: Scope, what: str) -> None:
    retry = limiter.check(scope.user_id)
    if retry is not None:
        raise ApiError(
            429,
            "rate_limited",
            f"Too many {what}: the limit is {limiter.limit} per minute.",
            headers={"Retry-After": str(math.ceil(retry))},
        )
