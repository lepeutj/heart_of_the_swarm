import asyncio
import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from heart_of_the_swarm import __version__
from heart_of_the_swarm.application import Application
from heart_of_the_swarm.authentication import AuthenticatedUser, AuthenticationError
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationService,
    DevelopmentPolicySource,
    Principal,
)
from heart_of_the_swarm.development_authorization import (
    grant_agent,
    grant_agent_version,
    grant_trace,
    grant_workflow,
    grant_workflow_version,
)
from heart_of_the_swarm.observability import (
    audit_event,
    audit_exception,
    get_trace_id,
    trace_context,
)
from heart_of_the_swarm.repositories import WorkflowRevisionConflict
from heart_of_the_swarm.skills import SkillDocument
from heart_of_the_swarm.spec import (
    AgentDetail,
    AgentRunAccepted,
    AgentRunDetail,
    AgentRunRequest,
    AgentSpec,
    AgentSummary,
    DesignRequest,
    DesignResponse,
    ModelDescriptor,
    RunEvent,
    RunStatus,
    TrajectoryStep,
    UsageSummary,
    ValidationResponse,
)
from heart_of_the_swarm.tools import (
    MCPConnection,
    MCPServerCreate,
    MCPServerDetail,
    MCPServerStatus,
    MCPServerTestResult,
    MCPServerView,
    ToolCatalogueResponse,
)
from heart_of_the_swarm.triggers import (
    TriggerDetail,
    TriggerDisabledError,
    TriggerInvocationError,
    TriggerNotFoundError,
    TriggerSpec,
)
from heart_of_the_swarm.validator import SpecValidationError
from heart_of_the_swarm.workflows import (
    WorkflowCapabilities,
    WorkflowSpec,
    WorkflowValidationError,
    WorkflowValidationResponse,
    workflow_capabilities,
)
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftDetail,
    WorkflowDraftSave,
    WorkflowSummary,
    WorkflowVersionDetail,
)
from heart_of_the_swarm.workflows.interruptions import (
    ApprovalResponse,
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import (
    WorkflowResumeRequest,
    WorkflowRunAccepted,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowRunRequest,
)

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
WORKFLOW_STREAM_TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
    RunStatus.INTERRUPTED,
}
WORKFLOW_STREAM_POLL_SECONDS = 0.25
WORKFLOW_STREAM_KEEPALIVE_POLLS = 60


@lru_cache
def get_application() -> Application:
    return Application(process="api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    application = get_application()
    await application.initialize()
    yield
    await application.close()


app = FastAPI(
    title="Heart of the Swarm",
    version=__version__,
    description="Design, configure, persist, and run task-specific agents.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
app.mount(
    "/workflow-editor",
    StaticFiles(directory=PACKAGE_DIR / "static" / "workflow-editor", html=True, check_dir=False),
    name="workflow-editor",
)


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    trace_id = str(uuid4())
    with trace_context(trace_id):
        audit_event(
            "http.request.started",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )
        try:
            response = await call_next(request)
        except Exception:
            audit_exception("http.request.failed", method=request.method, path=request.url.path)
            raise
        response.headers["X-Trace-ID"] = trace_id
        audit_event(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
        )
        return response


Runtime = Annotated[Application, Depends(get_application)]
bearer_scheme = HTTPBearer(auto_error=False)


async def authenticate_request(
    request: Request,
    runtime: Runtime,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> AuthenticatedUser:
    """Authenticate one protected request and expose its user principal to adapters."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        reason = "missing_bearer_token"
    else:
        try:
            user = runtime.authentication.authenticate(credentials.credentials)
        except AuthenticationError as exc:
            reason = exc.reason
        else:
            principal = user.to_principal()
            request.state.authenticated_user = user
            request.state.user_principal = principal
            audit_event(
                "http.authentication.succeeded",
                principal_kind=principal.kind,
                principal_id=principal.id,
            )
            return user
    audit_event("http.authentication.failed", level=logging.WARNING, reason=reason)
    raise HTTPException(
        status_code=401,
        detail="invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


api_router = APIRouter(dependencies=[Depends(authenticate_request)])


class RequestAuthorizer:
    """Authorize one authenticated HTTP principal without moving policy into routes."""

    def __init__(
        self,
        principal: Principal,
        service: AuthorizationService,
        development_source: DevelopmentPolicySource | None = None,
    ) -> None:
        self.principal = principal
        self.service = service
        self.development_source = development_source

    async def require(self, action: AuthorizationAction, resource: str) -> None:
        request = AuthorizationRequest(
            principal=self.principal,
            action=action,
            resource=resource,
        )
        decision = await self.service.decide(request)
        audit_event(
            "http.authorization.decided",
            level=logging.INFO if decision == AuthorizationDecision.ALLOW else logging.WARNING,
            principal_kind=self.principal.kind,
            principal_id=self.principal.id,
            action=action,
            resource=resource,
            decision=decision,
            reason="exact_policy" if decision == AuthorizationDecision.ALLOW else "policy_denied",
        )
        if decision != AuthorizationDecision.ALLOW:
            raise HTTPException(
                status_code=403,
                detail={"message": "access denied", "trace_id": get_trace_id()},
            )

    def grant_created(self, grant: Any, identifier: object) -> None:
        """Extend only the local development policy set after an authorized mutation."""
        if self.development_source is not None:
            grant(self.development_source, identifier)


async def get_request_authorizer(
    runtime: Runtime,
    user: Annotated[AuthenticatedUser, Depends(authenticate_request)],
) -> RequestAuthorizer:
    return RequestAuthorizer(user.to_principal(), runtime.authorization, runtime.policy_source)


Authorizer = Annotated[RequestAuthorizer, Depends(get_request_authorizer)]


def api_error(exc: Exception, status_code: int = 400) -> HTTPException:
    level = logging.WARNING if status_code < 500 else logging.ERROR
    audit_event(
        "api.request.rejected",
        level=level,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
    return HTTPException(
        status_code=status_code,
        detail={"message": str(exc), "trace_id": get_trace_id()},
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(runtime: Runtime) -> dict[str, str]:
    try:
        revision = await runtime.database.readiness()
    except Exception as exc:
        audit_event(
            "application.not_ready",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise HTTPException(status_code=503, detail="application is not ready") from exc
    return {"status": "ready", "database": "ready", "schema_revision": revision}


@api_router.get("/api/v1/providers")
async def providers(runtime: Runtime, authorizer: Authorizer) -> list[dict[str, str | bool]]:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    return runtime.providers.statuses()


@api_router.get("/api/v1/models", response_model=list[ModelDescriptor])
async def models(
    runtime: Runtime, authorizer: Authorizer, provider: str = Query(...)
) -> list[ModelDescriptor]:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    try:
        return await runtime.providers.list_models(provider)
    except Exception as exc:
        raise api_error(exc) from exc


@api_router.get("/api/v1/tools", response_model=ToolCatalogueResponse)
async def tools(runtime: Runtime, authorizer: Authorizer) -> ToolCatalogueResponse:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    return ToolCatalogueResponse(
        tools=runtime.tools.names,
        capabilities=tuple(capability.descriptor() for capability in runtime.tools.capabilities),
        mcp_servers=runtime.mcp_tools.statuses,
    )


def _mcp_server_view(
    server: MCPServerDetail,
    statuses: tuple[MCPServerStatus, ...],
) -> MCPServerView:
    status = next(
        (item for item in statuses if item.name == server.name),
        MCPServerStatus(name=server.name, state="not_loaded"),
    )
    return MCPServerView(**server.model_dump(), status=status)


@api_router.get("/api/v1/mcp/servers", response_model=list[MCPServerView])
async def list_mcp_servers(runtime: Runtime, authorizer: Authorizer) -> list[MCPServerView]:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    return [
        _mcp_server_view(server, runtime.mcp_tools.statuses)
        for server in await runtime.mcp_servers.list()
    ]


@api_router.post("/api/v1/mcp/servers", response_model=MCPServerView, status_code=201)
async def create_mcp_server(
    source: MCPServerCreate, runtime: Runtime, authorizer: Authorizer
) -> MCPServerView:
    await authorizer.require(AuthorizationAction.CAPABILITY_MANAGE, "capability:catalog")
    try:
        server = await runtime.mcp_servers.create(source)
        await runtime.sync_mcp_tools()
    except ValueError as exc:
        raise api_error(exc, 409) from exc
    if authorizer.development_source is not None:
        authorizer.development_source.grant_allow(
            AuthorizationAction.CAPABILITY_MANAGE,
            f"capability:mcp:{server.id}",
        )
    return _mcp_server_view(server, runtime.mcp_tools.statuses)


@api_router.post("/api/v1/mcp/servers/{server_id}/test", response_model=MCPServerTestResult)
async def test_mcp_server(
    server_id: UUID, runtime: Runtime, authorizer: Authorizer
) -> MCPServerTestResult:
    server = await runtime.mcp_servers.get(str(server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await authorizer.require(AuthorizationAction.CAPABILITY_MANAGE, f"capability:mcp:{server.id}")
    try:
        names = await runtime.mcp_tools.test(
            server.name,
            MCPConnection(
                url=server.url,
                bearer_credential_ref=server.bearer_credential_ref,
            ),
        )
    except Exception as exc:
        return MCPServerTestResult(
            name=server.name,
            reachable=False,
            error=f"MCP connection failed ({type(exc).__name__})",
        )
    return MCPServerTestResult(name=server.name, reachable=True, tools=names)


@api_router.post("/api/v1/mcp/servers/{server_id}/refresh", response_model=MCPServerView)
async def refresh_mcp_server(
    server_id: UUID, runtime: Runtime, authorizer: Authorizer
) -> MCPServerView:
    server = await runtime.mcp_servers.get(str(server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await authorizer.require(AuthorizationAction.CAPABILITY_MANAGE, f"capability:mcp:{server.id}")
    if not server.enabled:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    await runtime.mcp_tools.refresh(server.name)
    return _mcp_server_view(server, runtime.mcp_tools.statuses)


@api_router.get("/api/v1/skills")
async def skills(runtime: Runtime, authorizer: Authorizer) -> dict[str, tuple[str, ...]]:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    return {"skills": runtime.skills.names}


@api_router.post("/api/v1/skills", status_code=201)
async def upload_skill(
    skill: SkillDocument, runtime: Runtime, authorizer: Authorizer
) -> SkillDocument:
    await authorizer.require(AuthorizationAction.CAPABILITY_MANAGE, "capability:catalog")
    try:
        runtime.skills.save(skill)
    except ValueError as exc:
        raise api_error(exc, 409) from exc
    return skill


@api_router.get("/api/v1/workflows/capabilities", response_model=WorkflowCapabilities)
async def workflow_capability_catalogue(authorizer: Authorizer) -> WorkflowCapabilities:
    await authorizer.require(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    return workflow_capabilities()


@api_router.post("/api/v1/workflows/validate", response_model=WorkflowValidationResponse)
async def validate_workflow(
    payload: dict[str, Any], runtime: Runtime, authorizer: Authorizer
) -> WorkflowValidationResponse:
    await authorizer.require(AuthorizationAction.WORKFLOW_EDIT, "workflow:validation")
    try:
        spec = WorkflowSpec.model_validate(payload)
    except ValidationError as exc:
        issues = []
        nodes = payload.get("nodes")
        for error in exc.errors(include_url=False, include_context=False, include_input=False):
            location = error["loc"]
            node_id = None
            if (
                len(location) > 1
                and location[0] == "nodes"
                and isinstance(location[1], int)
                and isinstance(nodes, list)
                and location[1] < len(nodes)
                and isinstance(nodes[location[1]], dict)
            ):
                candidate = nodes[location[1]].get("id")
                node_id = candidate if isinstance(candidate, str) else None
            issues.append(
                {
                    "code": "workflow.schema.invalid",
                    "message": error["msg"],
                    "node_id": node_id,
                    "field": ".".join(str(part) for part in location),
                }
            )
        return WorkflowValidationResponse(valid=False, issues=issues)

    try:
        runtime.workflow_validator.validate(spec)
    except WorkflowValidationError as exc:
        return WorkflowValidationResponse(valid=False, issues=exc.issues)
    return WorkflowValidationResponse(valid=True, issues=[])


@api_router.get("/api/v1/workflows", response_model=list[WorkflowSummary])
async def list_workflows(runtime: Runtime, authorizer: Authorizer) -> list[WorkflowSummary]:
    await authorizer.require(AuthorizationAction.WORKFLOW_READ, "workflow:catalog")
    return await runtime.workflows.list()


@api_router.put("/api/v1/workflows/{workflow_id}", response_model=WorkflowDraftDetail)
async def save_workflow(
    workflow_id: UUID,
    draft: WorkflowDraftSave,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowDraftDetail:
    existing = await runtime.workflows.get(workflow_id)
    resource = f"workflow:{workflow_id}" if existing else "workflow:collection"
    await authorizer.require(AuthorizationAction.WORKFLOW_EDIT, resource)
    try:
        saved = await runtime.workflows.save(workflow_id, draft)
    except WorkflowRevisionConflict as exc:
        raise api_error(exc, 409) from exc
    except ValueError as exc:
        raise api_error(exc, 422) from exc
    authorizer.grant_created(grant_workflow, saved.id)
    return saved


@api_router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowDraftDetail)
async def get_workflow(
    workflow_id: UUID, runtime: Runtime, authorizer: Authorizer
) -> WorkflowDraftDetail:
    workflow = await runtime.workflows.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorizer.require(AuthorizationAction.WORKFLOW_READ, f"workflow:{workflow.id}")
    return workflow


@api_router.post(
    "/api/v1/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionDetail,
    status_code=201,
)
async def create_workflow_version(
    workflow_id: UUID,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowVersionDetail:
    workflow = await runtime.workflows.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorizer.require(AuthorizationAction.WORKFLOW_EDIT, f"workflow:{workflow.id}")
    try:
        version = await runtime.workflows.create_version(workflow_id)
    except WorkflowRevisionConflict as exc:
        raise api_error(exc, 409) from exc
    except (WorkflowValidationError, SpecValidationError, ValueError) as exc:
        raise api_error(exc, 422) from exc
    if version is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    authorizer.grant_created(grant_workflow_version, version.id)
    return version


@api_router.get(
    "/api/v1/workflows/{workflow_id}/versions/latest",
    response_model=WorkflowVersionDetail | None,
)
async def get_latest_workflow_version(
    workflow_id: UUID,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowVersionDetail | None:
    workflow = await runtime.workflows.get(workflow_id)
    if workflow is None:
        return None
    await authorizer.require(AuthorizationAction.WORKFLOW_READ, f"workflow:{workflow.id}")
    return await runtime.workflows.latest_version(workflow_id)


@api_router.post(
    "/api/v1/workflow-versions/{version_id}/runs",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def run_workflow_version(
    version_id: UUID,
    request: WorkflowRunRequest,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowRunAccepted:
    version = await runtime.workflows.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    await authorizer.require(AuthorizationAction.WORKFLOW_EXECUTE, f"workflow_version:{version.id}")
    try:
        accepted = await runtime.workflow_runs.queue(
            version_id,
            request.input,
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow version not found" else 422
        raise api_error(exc, status_code) from exc
    except (WorkflowValidationError, SpecValidationError) as exc:
        raise api_error(exc, 422) from exc
    authorizer.grant_created(grant_trace, accepted.trace_id)
    return accepted


@api_router.get("/api/v1/workflow-runs/{run_id}", response_model=WorkflowRunDetail)
async def get_workflow_run(
    run_id: UUID, runtime: Runtime, authorizer: Authorizer
) -> WorkflowRunDetail:
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    await authorizer.require(
        AuthorizationAction.WORKFLOW_READ,
        f"workflow_version:{run.workflow_version_id}",
    )
    return run


@api_router.post(
    "/api/v1/workflow-runs/{run_id}/resume",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def resume_workflow_run(
    run_id: UUID,
    request: WorkflowResumeRequest,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowRunAccepted:
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    await authorizer.require(
        AuthorizationAction.WORKFLOW_EXECUTE,
        f"workflow_version:{run.workflow_version_id}",
    )
    try:
        accepted = await runtime.workflow_runs.resume(
            run_id,
            request.checkpoint_id,
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow run not found" else 409
        raise api_error(exc, status_code) from exc
    authorizer.grant_created(grant_trace, accepted.trace_id)
    return accepted


@api_router.get(
    "/api/v1/workflow-runs/{run_id}/events",
    response_model=list[WorkflowRunEvent],
)
async def list_workflow_run_events(
    run_id: UUID,
    runtime: Runtime,
    authorizer: Authorizer,
) -> list[WorkflowRunEvent]:
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    await authorizer.require(AuthorizationAction.TRACE_READ, f"trace:{run.trace_id}")
    events = await runtime.workflow_runs.events(run_id)
    if events is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return events


@api_router.get("/api/v1/workflow-runs/{run_id}/stream")
async def stream_workflow_run_events(
    run_id: UUID,
    runtime: Runtime,
    authorizer: Authorizer,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> StreamingResponse:
    """Stream ordered durable run events and support SSE cursor reconnection."""
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    await authorizer.require(AuthorizationAction.TRACE_READ, f"trace:{run.trace_id}")
    cursor = last_event_id or 0

    async def event_stream():
        nonlocal cursor
        idle_polls = 0
        while True:
            batch = await runtime.workflow_runs.event_batch(run_id, cursor)
            if batch is None:
                return
            for event in batch.events:
                cursor = event.sequence
                envelope = {
                    "event_id": str(event.id),
                    "run_id": str(event.workflow_run_id),
                    "thread_id": str(batch.thread_id) if batch.thread_id else None,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "data": event.data,
                    "created_at": event.created_at.isoformat(),
                }
                yield (
                    f"id: {event.sequence}\n"
                    "event: workflow.event\n"
                    f"data: {json.dumps(envelope, separators=(',', ':'))}\n\n"
                )
            if batch.status in WORKFLOW_STREAM_TERMINAL_STATUSES:
                return
            if batch.events:
                idle_polls = 0
            else:
                idle_polls += 1
                if idle_polls >= WORKFLOW_STREAM_KEEPALIVE_POLLS:
                    yield ": keepalive\n\n"
                    idle_polls = 0
            await asyncio.sleep(WORKFLOW_STREAM_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_router.get(
    "/api/v1/workflow-interruptions",
    response_model=list[WorkflowInterruptionDetail],
)
async def list_workflow_interruptions(
    runtime: Runtime,
    authorizer: Authorizer,
    status: WorkflowInterruptionStatus | None = WorkflowInterruptionStatus.PENDING,
) -> list[WorkflowInterruptionDetail]:
    await authorizer.require(AuthorizationAction.WORKFLOW_READ, "workflow:interruptions")
    return await runtime.workflow_approvals.list(status)


@api_router.get(
    "/api/v1/workflow-interruptions/{interruption_id}",
    response_model=WorkflowInterruptionDetail,
)
async def get_workflow_interruption(
    interruption_id: UUID,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowInterruptionDetail:
    interruption = await runtime.workflow_approvals.get(interruption_id)
    if interruption is None:
        raise HTTPException(status_code=404, detail="workflow interruption not found")
    await authorizer.require(
        AuthorizationAction.WORKFLOW_READ,
        f"workflow_version:{interruption.workflow_version_id}",
    )
    return interruption


@api_router.post(
    "/api/v1/workflow-interruptions/{interruption_id}/response",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def respond_to_workflow_interruption(
    interruption_id: UUID,
    response: ApprovalResponse,
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowRunAccepted:
    interruption = await runtime.workflow_approvals.get(interruption_id)
    if interruption is None:
        raise HTTPException(status_code=404, detail="workflow interruption not found")
    await authorizer.require(
        AuthorizationAction.WORKFLOW_EXECUTE,
        f"workflow_version:{interruption.workflow_version_id}",
    )
    try:
        accepted = await runtime.workflow_approvals.respond(
            interruption_id,
            response.model_dump(mode="json"),
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow interruption not found" else 409
        raise api_error(exc, status_code) from exc
    authorizer.grant_created(grant_trace, accepted.trace_id)
    return accepted


@api_router.post("/api/v1/triggers", response_model=TriggerDetail, status_code=201)
async def create_trigger(
    spec: TriggerSpec, runtime: Runtime, authorizer: Authorizer
) -> TriggerDetail:
    if spec.target_type == "workflow":
        target = await runtime.workflows.get_version(spec.target_version_id)
        action = AuthorizationAction.WORKFLOW_EDIT
    else:
        target = await runtime.agents.get_version(str(spec.target_version_id))
        action = AuthorizationAction.AGENT_EDIT
    if target is None:
        raise HTTPException(status_code=404, detail="trigger target version not found")
    await authorizer.require(action, f"{spec.target_type}_version:{target.id}")
    try:
        return await runtime.triggers.create(spec)
    except ValueError as exc:
        raise api_error(exc, 422) from exc


@api_router.get("/api/v1/triggers", response_model=list[TriggerDetail])
async def list_triggers(runtime: Runtime, authorizer: Authorizer) -> list[TriggerDetail]:
    await authorizer.require(AuthorizationAction.WORKFLOW_READ, "workflow:triggers")
    return await runtime.triggers.list()


@api_router.get("/api/v1/triggers/{trigger_id}", response_model=TriggerDetail)
async def get_trigger(trigger_id: UUID, runtime: Runtime, authorizer: Authorizer) -> TriggerDetail:
    trigger = await runtime.triggers.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    action = (
        AuthorizationAction.WORKFLOW_READ
        if trigger.target_type == "workflow"
        else AuthorizationAction.AGENT_READ
    )
    await authorizer.require(action, f"{trigger.target_type}_version:{trigger.target_version_id}")
    return trigger


@api_router.post(
    "/api/v1/hooks/{trigger_id}",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def invoke_webhook(
    trigger_id: UUID,
    payload: dict[str, Any],
    runtime: Runtime,
    authorizer: Authorizer,
) -> WorkflowRunAccepted:
    trigger = await runtime.triggers.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    action = (
        AuthorizationAction.WORKFLOW_EXECUTE
        if trigger.target_type == "workflow"
        else AuthorizationAction.AGENT_EXECUTE
    )
    await authorizer.require(action, f"{trigger.target_type}_version:{trigger.target_version_id}")
    try:
        accepted = await runtime.webhook_triggers.invoke(
            trigger_id,
            payload,
            get_trace_id() or str(uuid4()),
        )
    except TriggerNotFoundError as exc:
        raise api_error(exc, 404) from exc
    except TriggerDisabledError as exc:
        raise api_error(exc, 409) from exc
    except (TriggerInvocationError, WorkflowValidationError, SpecValidationError) as exc:
        raise api_error(exc, 422) from exc
    except ValueError as exc:
        raise api_error(exc, 422) from exc
    authorizer.grant_created(grant_trace, accepted.trace_id)
    return accepted


@api_router.post("/api/v1/agents/design", response_model=DesignResponse)
async def design_agent(
    request: DesignRequest, runtime: Runtime, authorizer: Authorizer
) -> DesignResponse:
    await authorizer.require(AuthorizationAction.AGENT_EDIT, "agent:builder")
    try:
        return await runtime.agents.design(request)
    except Exception as exc:
        raise api_error(exc, 502) from exc


@api_router.post("/api/v1/agents/validate", response_model=ValidationResponse)
async def validate_agent(
    spec: AgentSpec, runtime: Runtime, authorizer: Authorizer
) -> ValidationResponse:
    await authorizer.require(AuthorizationAction.AGENT_EDIT, "agent:validation")
    errors = await runtime.agents.validation_errors(spec)
    return ValidationResponse(valid=not errors, errors=errors)


@api_router.post("/api/v1/agents", response_model=AgentDetail, status_code=201)
async def create_agent(spec: AgentSpec, runtime: Runtime, authorizer: Authorizer) -> AgentDetail:
    await authorizer.require(AuthorizationAction.AGENT_EDIT, "agent:collection")
    try:
        agent = await runtime.agents.create_agent(spec)
    except SpecValidationError as exc:
        raise api_error(exc, 422) from exc
    authorizer.grant_created(grant_agent, agent.id)
    authorizer.grant_created(grant_agent_version, agent.version_id)
    return agent


@api_router.get("/api/v1/agents", response_model=list[AgentSummary])
async def list_agents(runtime: Runtime, authorizer: Authorizer) -> list[AgentSummary]:
    await authorizer.require(AuthorizationAction.AGENT_READ, "agent:catalog")
    return await runtime.agents.list_agents()


@api_router.post("/api/v1/agents/{agent_id}/versions", response_model=AgentDetail, status_code=201)
async def add_agent_version(
    agent_id: str, spec: AgentSpec, runtime: Runtime, authorizer: Authorizer
) -> AgentDetail:
    agent_record = await runtime.agents.get_agent(agent_id)
    if agent_record is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await authorizer.require(AuthorizationAction.AGENT_EDIT, f"agent:{agent_record.id}")
    try:
        agent = await runtime.agents.add_version(agent_id, spec)
    except SpecValidationError as exc:
        raise api_error(exc, 422) from exc
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    authorizer.grant_created(grant_agent_version, agent.version_id)
    return agent


@api_router.get("/api/v1/agents/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: str, runtime: Runtime, authorizer: Authorizer) -> AgentDetail:
    agent = await runtime.agents.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await authorizer.require(AuthorizationAction.AGENT_READ, f"agent:{agent.id}")
    return agent


@api_router.post(
    "/api/v1/agents/{agent_id}/runs",
    response_model=AgentRunAccepted,
    status_code=202,
)
async def run_agent(
    agent_id: str,
    request: AgentRunRequest,
    runtime: Runtime,
    authorizer: Authorizer,
) -> AgentRunAccepted:
    agent = await runtime.agents.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await authorizer.require(AuthorizationAction.AGENT_EXECUTE, f"agent_version:{agent.version_id}")
    try:
        accepted = await runtime.runs.queue(
            agent.version_id,
            request.input,
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        raise api_error(exc, 404 if str(exc) == "agent version not found" else 422) from exc
    except Exception as exc:
        audit_exception("run.failed", error_type=type(exc).__name__, error_message=str(exc))
        raise HTTPException(
            status_code=502,
            detail={"message": "agent execution failed", "trace_id": get_trace_id()},
        ) from exc
    authorizer.grant_created(grant_trace, accepted.trace_id)
    return accepted


@api_router.get("/api/v1/runs/{run_id}", response_model=AgentRunDetail)
async def get_run(run_id: str, runtime: Runtime, authorizer: Authorizer) -> AgentRunDetail:
    run = await runtime.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    agent = await runtime.agents.get_agent_version(run.agent_id, run.agent_version)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent version not found")
    await authorizer.require(AuthorizationAction.AGENT_READ, f"agent_version:{agent.version_id}")
    return run


@api_router.get("/api/v1/agents/{agent_id}/runs", response_model=list[AgentRunDetail])
async def list_agent_runs(
    agent_id: str, runtime: Runtime, authorizer: Authorizer
) -> list[AgentRunDetail]:
    agent = await runtime.agents.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await authorizer.require(AuthorizationAction.AGENT_READ, f"agent:{agent.id}")
    return await runtime.runs.list_runs(agent_id)


@api_router.get("/api/v1/runs/{run_id}/events", response_model=list[RunEvent])
async def list_run_events(run_id: str, runtime: Runtime, authorizer: Authorizer) -> list[RunEvent]:
    run = await runtime.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await authorizer.require(AuthorizationAction.TRACE_READ, f"trace:{run.trace_id}")
    events = await runtime.runs.events(run_id)
    if events is None:
        raise HTTPException(status_code=404, detail="run not found")
    return events


@api_router.get("/api/v1/runs/{run_id}/trajectory", response_model=list[TrajectoryStep])
async def get_run_trajectory(
    run_id: str, runtime: Runtime, authorizer: Authorizer
) -> list[TrajectoryStep]:
    run = await runtime.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await authorizer.require(AuthorizationAction.TRACE_READ, f"trace:{run.trace_id}")
    trajectory = await runtime.runs.trajectory(run_id)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="run not found")
    return trajectory


@api_router.post("/api/v1/runs/{run_id}/cancel", response_model=AgentRunDetail)
async def cancel_run(run_id: str, runtime: Runtime, authorizer: Authorizer) -> AgentRunDetail:
    existing = await runtime.runs.get(run_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="run not found")
    agent = await runtime.agents.get_agent_version(existing.agent_id, existing.agent_version)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent version not found")
    await authorizer.require(AuthorizationAction.AGENT_EXECUTE, f"agent_version:{agent.version_id}")
    run = await runtime.runs.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@api_router.get("/api/v1/usage/summary", response_model=list[UsageSummary])
async def usage_summary(runtime: Runtime, authorizer: Authorizer) -> list[UsageSummary]:
    await authorizer.require(AuthorizationAction.TRACE_READ, "trace:usage")
    return await runtime.agents.usage_summary()


app.include_router(api_router)
