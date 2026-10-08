from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest

from heart_of_the_swarm.api import (
    design_agent,
    get_agent,
    get_run_trajectory,
    get_workflow,
    list_workflow_interruptions,
    list_workflow_run_events,
    list_workflows,
    providers,
    run_agent,
    run_workflow_version,
    usage_summary,
)
from heart_of_the_swarm.api import (
    test_mcp_server as mcp_server_test_endpoint,
)
from heart_of_the_swarm.authorization import AuthorizationAction
from heart_of_the_swarm.spec import AgentRunRequest, DesignRequest
from heart_of_the_swarm.workflows.runs import WorkflowRunRequest

OBJECT_ID = UUID("11850612-0402-418e-bee3-f4603a72d4eb")
RUN_ID = UUID("293ce849-ef4a-468b-ae41-21c92bcdd10e")


@dataclass(frozen=True)
class AuthorizationCase:
    method: str
    endpoint: str
    action: AuthorizationAction
    resource: str


class RecordingAuthorizer:
    development_source = None

    def __init__(self) -> None:
        self.requests: list[tuple[AuthorizationAction, str]] = []

    async def require(self, action: AuthorizationAction, resource: str) -> None:
        self.requests.append((action, resource))

    def grant_created(self, _grant, _identifier) -> None:
        pass


class FakeAgents:
    async def design(self, _request):
        return None

    async def get_agent(self, _agent_id):
        return SimpleNamespace(id="agent-1", version_id="agent-version-1")

    async def usage_summary(self):
        return []


class FakeRuns:
    async def queue(self, version_id, _agent_input, _trace_id):
        assert version_id == "agent-version-1"
        return SimpleNamespace(trace_id="trace-new")

    async def get(self, _run_id):
        return SimpleNamespace(
            agent_id="agent-1",
            agent_version=1,
            trace_id="trace-agent-run",
        )

    async def trajectory(self, _run_id):
        return []


class FakeWorkflows:
    async def list(self):
        return []

    async def get(self, _workflow_id):
        return SimpleNamespace(id=OBJECT_ID)

    async def get_version(self, _version_id):
        return SimpleNamespace(id=OBJECT_ID)


class FakeWorkflowRuns:
    async def queue(self, _version_id, _input, _trace_id):
        return SimpleNamespace(trace_id="trace-new")

    async def get(self, _run_id):
        return SimpleNamespace(
            workflow_version_id=OBJECT_ID,
            trace_id="trace-workflow-run",
        )

    async def events(self, _run_id):
        return []


class FakeApprovals:
    async def list(self, _status):
        return []


class FakeMCPServers:
    async def get(self, _server_id):
        return SimpleNamespace(id=OBJECT_ID, name="github", url="https://mcp.example.test")


class FakeMCPTools:
    async def test(self, _name, _url):
        return ()


def runtime() -> SimpleNamespace:
    return SimpleNamespace(
        providers=SimpleNamespace(statuses=lambda: []),
        agents=FakeAgents(),
        runs=FakeRuns(),
        workflows=FakeWorkflows(),
        workflow_runs=FakeWorkflowRuns(),
        workflow_approvals=FakeApprovals(),
        mcp_servers=FakeMCPServers(),
        mcp_tools=FakeMCPTools(),
    )


CASES = (
    AuthorizationCase(
        "GET", "/api/v1/providers", AuthorizationAction.CAPABILITY_READ, "capability:catalog"
    ),
    AuthorizationCase(
        "POST",
        "/api/v1/mcp/servers/{id}/test",
        AuthorizationAction.CAPABILITY_MANAGE,
        f"capability:mcp:{OBJECT_ID}",
    ),
    AuthorizationCase(
        "GET", "/api/v1/workflows", AuthorizationAction.WORKFLOW_READ, "workflow:catalog"
    ),
    AuthorizationCase(
        "GET", "/api/v1/workflows/{id}", AuthorizationAction.WORKFLOW_READ, f"workflow:{OBJECT_ID}"
    ),
    AuthorizationCase(
        "POST",
        "/api/v1/workflow-versions/{id}/runs",
        AuthorizationAction.WORKFLOW_EXECUTE,
        f"workflow_version:{OBJECT_ID}",
    ),
    AuthorizationCase(
        "GET",
        "/api/v1/workflow-runs/{id}/events",
        AuthorizationAction.TRACE_READ,
        "trace:trace-workflow-run",
    ),
    AuthorizationCase(
        "GET",
        "/api/v1/workflow-interruptions",
        AuthorizationAction.WORKFLOW_READ,
        "workflow:interruptions",
    ),
    AuthorizationCase(
        "POST", "/api/v1/agents/design", AuthorizationAction.AGENT_EDIT, "agent:builder"
    ),
    AuthorizationCase(
        "GET", "/api/v1/agents/{id}", AuthorizationAction.AGENT_READ, "agent:agent-1"
    ),
    AuthorizationCase(
        "POST",
        "/api/v1/agents/{id}/runs",
        AuthorizationAction.AGENT_EXECUTE,
        "agent_version:agent-version-1",
    ),
    AuthorizationCase(
        "GET",
        "/api/v1/runs/{id}/trajectory",
        AuthorizationAction.TRACE_READ,
        "trace:trace-agent-run",
    ),
    AuthorizationCase(
        "GET", "/api/v1/usage/summary", AuthorizationAction.TRACE_READ, "trace:usage"
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.method} {case.endpoint}")
async def test_http_operation_uses_its_declared_authorization_contract(
    case: AuthorizationCase,
) -> None:
    authorizer = RecordingAuthorizer()
    app = runtime()

    if case.endpoint == "/api/v1/providers":
        await providers(app, authorizer)
    elif case.endpoint == "/api/v1/mcp/servers/{id}/test":
        await mcp_server_test_endpoint(OBJECT_ID, app, authorizer)
    elif case.endpoint == "/api/v1/workflows":
        await list_workflows(app, authorizer)
    elif case.endpoint == "/api/v1/workflows/{id}":
        await get_workflow(OBJECT_ID, app, authorizer)
    elif case.endpoint == "/api/v1/workflow-versions/{id}/runs":
        await run_workflow_version(
            OBJECT_ID,
            WorkflowRunRequest(input={}),
            app,
            authorizer,
        )
    elif case.endpoint == "/api/v1/workflow-runs/{id}/events":
        await list_workflow_run_events(RUN_ID, app, authorizer)
    elif case.endpoint == "/api/v1/workflow-interruptions":
        await list_workflow_interruptions(app, authorizer)
    elif case.endpoint == "/api/v1/agents/design":
        await design_agent(DesignRequest(task="Design an agent"), app, authorizer)
    elif case.endpoint == "/api/v1/agents/{id}":
        await get_agent("agent-1", app, authorizer)
    elif case.endpoint == "/api/v1/agents/{id}/runs":
        await run_agent("agent-1", AgentRunRequest(input="Run it"), app, authorizer)
    elif case.endpoint == "/api/v1/runs/{id}/trajectory":
        await get_run_trajectory("run-1", app, authorizer)
    elif case.endpoint == "/api/v1/usage/summary":
        await usage_summary(app, authorizer)
    else:
        raise AssertionError(f"Unhandled authorization case: {case.endpoint}")

    assert authorizer.requests == [(case.action, case.resource)]
