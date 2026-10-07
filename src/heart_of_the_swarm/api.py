import asyncio
import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from heart_of_the_swarm import __version__
from heart_of_the_swarm.application import Application
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


@app.get("/api/v1/providers")
async def providers(runtime: Runtime) -> list[dict[str, str | bool]]:
    return runtime.providers.statuses()


@app.get("/api/v1/models", response_model=list[ModelDescriptor])
async def models(runtime: Runtime, provider: str = Query(...)) -> list[ModelDescriptor]:
    try:
        return await runtime.providers.list_models(provider)
    except Exception as exc:
        raise api_error(exc) from exc


@app.get("/api/v1/tools", response_model=ToolCatalogueResponse)
async def tools(runtime: Runtime) -> ToolCatalogueResponse:
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


@app.get("/api/v1/mcp/servers", response_model=list[MCPServerView])
async def list_mcp_servers(runtime: Runtime) -> list[MCPServerView]:
    return [
        _mcp_server_view(server, runtime.mcp_tools.statuses)
        for server in await runtime.mcp_servers.list()
    ]


@app.post("/api/v1/mcp/servers", response_model=MCPServerView, status_code=201)
async def create_mcp_server(source: MCPServerCreate, runtime: Runtime) -> MCPServerView:
    try:
        server = await runtime.mcp_servers.create(source)
        await runtime.sync_mcp_tools()
    except ValueError as exc:
        raise api_error(exc, 409) from exc
    return _mcp_server_view(server, runtime.mcp_tools.statuses)


@app.post("/api/v1/mcp/servers/{server_id}/test", response_model=MCPServerTestResult)
async def test_mcp_server(server_id: UUID, runtime: Runtime) -> MCPServerTestResult:
    server = await runtime.mcp_servers.get(str(server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        names = await runtime.mcp_tools.test(server.name, server.url)
    except Exception as exc:
        return MCPServerTestResult(
            name=server.name,
            reachable=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return MCPServerTestResult(name=server.name, reachable=True, tools=names)


@app.post("/api/v1/mcp/servers/{server_id}/refresh", response_model=MCPServerView)
async def refresh_mcp_server(server_id: UUID, runtime: Runtime) -> MCPServerView:
    server = await runtime.mcp_servers.get(str(server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if not server.enabled:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    await runtime.mcp_tools.refresh(server.name)
    return _mcp_server_view(server, runtime.mcp_tools.statuses)


@app.get("/api/v1/skills")
async def skills(runtime: Runtime) -> dict[str, tuple[str, ...]]:
    return {"skills": runtime.skills.names}


@app.post("/api/v1/skills", status_code=201)
async def upload_skill(skill: SkillDocument, runtime: Runtime) -> SkillDocument:
    try:
        runtime.skills.save(skill)
    except ValueError as exc:
        raise api_error(exc, 409) from exc
    return skill


@app.get("/api/v1/workflows/capabilities", response_model=WorkflowCapabilities)
async def workflow_capability_catalogue() -> WorkflowCapabilities:
    return workflow_capabilities()


@app.post("/api/v1/workflows/validate", response_model=WorkflowValidationResponse)
async def validate_workflow(
    payload: dict[str, Any], runtime: Runtime
) -> WorkflowValidationResponse:
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


@app.get("/api/v1/workflows", response_model=list[WorkflowSummary])
async def list_workflows(runtime: Runtime) -> list[WorkflowSummary]:
    return await runtime.workflows.list()


@app.put("/api/v1/workflows/{workflow_id}", response_model=WorkflowDraftDetail)
async def save_workflow(
    workflow_id: UUID,
    draft: WorkflowDraftSave,
    runtime: Runtime,
) -> WorkflowDraftDetail:
    try:
        return await runtime.workflows.save(workflow_id, draft)
    except WorkflowRevisionConflict as exc:
        raise api_error(exc, 409) from exc
    except ValueError as exc:
        raise api_error(exc, 422) from exc


@app.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowDraftDetail)
async def get_workflow(workflow_id: UUID, runtime: Runtime) -> WorkflowDraftDetail:
    workflow = await runtime.workflows.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@app.post(
    "/api/v1/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionDetail,
    status_code=201,
)
async def create_workflow_version(
    workflow_id: UUID,
    runtime: Runtime,
) -> WorkflowVersionDetail:
    try:
        version = await runtime.workflows.create_version(workflow_id)
    except WorkflowRevisionConflict as exc:
        raise api_error(exc, 409) from exc
    except (WorkflowValidationError, SpecValidationError, ValueError) as exc:
        raise api_error(exc, 422) from exc
    if version is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return version


@app.get(
    "/api/v1/workflows/{workflow_id}/versions/latest",
    response_model=WorkflowVersionDetail | None,
)
async def get_latest_workflow_version(
    workflow_id: UUID,
    runtime: Runtime,
) -> WorkflowVersionDetail | None:
    return await runtime.workflows.latest_version(workflow_id)


@app.post(
    "/api/v1/workflow-versions/{version_id}/runs",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def run_workflow_version(
    version_id: UUID,
    request: WorkflowRunRequest,
    runtime: Runtime,
) -> WorkflowRunAccepted:
    try:
        return await runtime.workflow_runs.queue(
            version_id,
            request.input,
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow version not found" else 422
        raise api_error(exc, status_code) from exc
    except (WorkflowValidationError, SpecValidationError) as exc:
        raise api_error(exc, 422) from exc


@app.get("/api/v1/workflow-runs/{run_id}", response_model=WorkflowRunDetail)
async def get_workflow_run(run_id: UUID, runtime: Runtime) -> WorkflowRunDetail:
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return run


@app.post(
    "/api/v1/workflow-runs/{run_id}/resume",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def resume_workflow_run(
    run_id: UUID,
    request: WorkflowResumeRequest,
    runtime: Runtime,
) -> WorkflowRunAccepted:
    try:
        return await runtime.workflow_runs.resume(
            run_id,
            request.checkpoint_id,
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow run not found" else 409
        raise api_error(exc, status_code) from exc


@app.get(
    "/api/v1/workflow-runs/{run_id}/events",
    response_model=list[WorkflowRunEvent],
)
async def list_workflow_run_events(
    run_id: UUID,
    runtime: Runtime,
) -> list[WorkflowRunEvent]:
    events = await runtime.workflow_runs.events(run_id)
    if events is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return events


@app.get("/api/v1/workflow-runs/{run_id}/stream")
async def stream_workflow_run_events(
    run_id: UUID,
    runtime: Runtime,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> StreamingResponse:
    """Stream ordered durable run events and support SSE cursor reconnection."""
    run = await runtime.workflow_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
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


@app.get(
    "/api/v1/workflow-interruptions",
    response_model=list[WorkflowInterruptionDetail],
)
async def list_workflow_interruptions(
    runtime: Runtime,
    status: WorkflowInterruptionStatus | None = WorkflowInterruptionStatus.PENDING,
) -> list[WorkflowInterruptionDetail]:
    return await runtime.workflow_approvals.list(status)


@app.get(
    "/api/v1/workflow-interruptions/{interruption_id}",
    response_model=WorkflowInterruptionDetail,
)
async def get_workflow_interruption(
    interruption_id: UUID,
    runtime: Runtime,
) -> WorkflowInterruptionDetail:
    interruption = await runtime.workflow_approvals.get(interruption_id)
    if interruption is None:
        raise HTTPException(status_code=404, detail="workflow interruption not found")
    return interruption


@app.post(
    "/api/v1/workflow-interruptions/{interruption_id}/response",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def respond_to_workflow_interruption(
    interruption_id: UUID,
    response: ApprovalResponse,
    runtime: Runtime,
) -> WorkflowRunAccepted:
    try:
        return await runtime.workflow_approvals.respond(
            interruption_id,
            response.model_dump(mode="json"),
            get_trace_id() or str(uuid4()),
        )
    except ValueError as exc:
        status_code = 404 if str(exc) == "workflow interruption not found" else 409
        raise api_error(exc, status_code) from exc


@app.post("/api/v1/triggers", response_model=TriggerDetail, status_code=201)
async def create_trigger(spec: TriggerSpec, runtime: Runtime) -> TriggerDetail:
    try:
        return await runtime.triggers.create(spec)
    except ValueError as exc:
        raise api_error(exc, 422) from exc


@app.get("/api/v1/triggers", response_model=list[TriggerDetail])
async def list_triggers(runtime: Runtime) -> list[TriggerDetail]:
    return await runtime.triggers.list()


@app.get("/api/v1/triggers/{trigger_id}", response_model=TriggerDetail)
async def get_trigger(trigger_id: UUID, runtime: Runtime) -> TriggerDetail:
    trigger = await runtime.triggers.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    return trigger


@app.post(
    "/api/v1/hooks/{trigger_id}",
    response_model=WorkflowRunAccepted,
    status_code=202,
)
async def invoke_webhook(
    trigger_id: UUID,
    payload: dict[str, Any],
    runtime: Runtime,
) -> WorkflowRunAccepted:
    try:
        return await runtime.webhook_triggers.invoke(
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


@app.post("/api/v1/agents/design", response_model=DesignResponse)
async def design_agent(request: DesignRequest, runtime: Runtime) -> DesignResponse:
    try:
        return await runtime.agents.design(request)
    except Exception as exc:
        raise api_error(exc, 502) from exc


@app.post("/api/v1/agents/validate", response_model=ValidationResponse)
async def validate_agent(spec: AgentSpec, runtime: Runtime) -> ValidationResponse:
    errors = await runtime.agents.validation_errors(spec)
    return ValidationResponse(valid=not errors, errors=errors)


@app.post("/api/v1/agents", response_model=AgentDetail, status_code=201)
async def create_agent(spec: AgentSpec, runtime: Runtime) -> AgentDetail:
    try:
        return await runtime.agents.create_agent(spec)
    except SpecValidationError as exc:
        raise api_error(exc, 422) from exc


@app.get("/api/v1/agents", response_model=list[AgentSummary])
async def list_agents(runtime: Runtime) -> list[AgentSummary]:
    return await runtime.agents.list_agents()


@app.post("/api/v1/agents/{agent_id}/versions", response_model=AgentDetail, status_code=201)
async def add_agent_version(agent_id: str, spec: AgentSpec, runtime: Runtime) -> AgentDetail:
    try:
        agent = await runtime.agents.add_version(agent_id, spec)
    except SpecValidationError as exc:
        raise api_error(exc, 422) from exc
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@app.get("/api/v1/agents/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: str, runtime: Runtime) -> AgentDetail:
    agent = await runtime.agents.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@app.post(
    "/api/v1/agents/{agent_id}/runs",
    response_model=AgentRunAccepted,
    status_code=202,
)
async def run_agent(agent_id: str, request: AgentRunRequest, runtime: Runtime) -> AgentRunAccepted:
    try:
        return await runtime.runs.queue(agent_id, request.input, get_trace_id() or str(uuid4()))
    except ValueError as exc:
        raise api_error(exc, 404 if str(exc) == "agent not found" else 422) from exc
    except Exception as exc:
        audit_exception("run.failed", error_type=type(exc).__name__, error_message=str(exc))
        raise HTTPException(
            status_code=502,
            detail={"message": "agent execution failed", "trace_id": get_trace_id()},
        ) from exc


@app.get("/api/v1/runs/{run_id}", response_model=AgentRunDetail)
async def get_run(run_id: str, runtime: Runtime) -> AgentRunDetail:
    run = await runtime.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/api/v1/agents/{agent_id}/runs", response_model=list[AgentRunDetail])
async def list_agent_runs(agent_id: str, runtime: Runtime) -> list[AgentRunDetail]:
    return await runtime.runs.list_runs(agent_id)


@app.get("/api/v1/runs/{run_id}/events", response_model=list[RunEvent])
async def list_run_events(run_id: str, runtime: Runtime) -> list[RunEvent]:
    events = await runtime.runs.events(run_id)
    if events is None:
        raise HTTPException(status_code=404, detail="run not found")
    return events


@app.get("/api/v1/runs/{run_id}/trajectory", response_model=list[TrajectoryStep])
async def get_run_trajectory(run_id: str, runtime: Runtime) -> list[TrajectoryStep]:
    trajectory = await runtime.runs.trajectory(run_id)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="run not found")
    return trajectory


@app.post("/api/v1/runs/{run_id}/cancel", response_model=AgentRunDetail)
async def cancel_run(run_id: str, runtime: Runtime) -> AgentRunDetail:
    run = await runtime.runs.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/api/v1/usage/summary", response_model=list[UsageSummary])
async def usage_summary(runtime: Runtime) -> list[UsageSummary]:
    return await runtime.agents.usage_summary()
