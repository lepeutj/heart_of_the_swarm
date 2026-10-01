import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
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
    TrajectoryStep,
    UsageSummary,
    ValidationResponse,
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

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


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


@app.get("/api/v1/tools")
async def tools(runtime: Runtime) -> dict[str, object]:
    return {
        "tools": runtime.tools.names,
        "capabilities": [
            {
                "id": capability.id,
                "source": capability.source,
                "origin": capability.origin,
                "description": capability.tool.description,
            }
            for capability in runtime.tools.capabilities
        ],
    }


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
