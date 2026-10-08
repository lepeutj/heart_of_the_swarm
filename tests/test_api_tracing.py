import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import tool

from heart_of_the_swarm import __version__
from heart_of_the_swarm.api import (
    RequestAuthorizer,
    app,
    authenticate_request,
    get_application,
    get_request_authorizer,
)
from heart_of_the_swarm.authentication import AuthenticatedUser, AuthenticationMethod
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationService,
    DevelopmentPolicySource,
)
from heart_of_the_swarm.skills import SkillRegistry
from heart_of_the_swarm.spec import AgentRunAccepted
from heart_of_the_swarm.tools import RegisteredCapability, ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.interruptions import (
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import (
    WorkflowRunAccepted,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowRunEventBatch,
)


@asynccontextmanager
async def isolated_lifespan(_app):
    """Exercise API routes without starting PostgreSQL, MCP, or MLflow adapters."""
    yield


class AllowRequestAuthorizer:
    development_source = None

    async def require(self, _action, _resource) -> None:
        pass

    def grant_created(self, _grant, _identifier) -> None:
        pass


@pytest.fixture(autouse=True)
def isolate_application_lifespan():
    original = app.router.lifespan_context
    app.router.lifespan_context = isolated_lifespan
    app.dependency_overrides[authenticate_request] = lambda: AuthenticatedUser(
        subject="test-user",
        issuer="tests",
        method=AuthenticationMethod.DEVELOPMENT,
    )
    app.dependency_overrides[get_request_authorizer] = AllowRequestAuthorizer
    try:
        yield
    finally:
        app.router.lifespan_context = original
        app.dependency_overrides.clear()


@tool
def get_weather(city: str) -> str:
    """Return test weather for one city."""
    return city


def test_health_response_has_trace_id_header() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    UUID(response.headers["X-Trace-ID"])


def test_ui_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Heart of the Swarm" in response.text
    assert 'href="/workflow-editor/"' in response.text


def test_workflow_editor_build_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/workflow-editor/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_markdown_skill_can_be_uploaded_and_listed(tmp_path) -> None:
    runtime = SimpleNamespace(skills=SkillRegistry(tmp_path))
    app.dependency_overrides[get_application] = lambda: runtime
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/skills",
                json={"name": "research", "content": "Verify multiple sources."},
            )
            listed = client.get("/api/v1/skills")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert listed.json() == {"skills": ["research"]}
    assert (tmp_path / "research.md").read_text(encoding="utf-8") == "Verify multiple sources."


def test_api_version_comes_from_the_package() -> None:
    assert app.version == __version__


def test_authorization_denial_prevents_a_catalogue_read() -> None:
    policies = DevelopmentPolicySource("test-user")
    authorizer = RequestAuthorizer(policies.principal, AuthorizationService(policies), policies)
    provider_calls = 0

    class FakeProviders:
        def statuses(self):
            nonlocal provider_calls
            provider_calls += 1
            return []

    app.dependency_overrides[get_request_authorizer] = lambda: authorizer
    app.dependency_overrides[get_application] = lambda: SimpleNamespace(providers=FakeProviders())
    try:
        with TestClient(app) as client:
            denied = client.get("/api/v1/providers")
            assert provider_calls == 0
            policies.grant(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
            allowed = client.get("/api/v1/providers")
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 403
    assert denied.json()["detail"]["message"] == "access denied"
    assert provider_calls == 1
    assert allowed.status_code == 200


def test_readiness_reports_database_revision() -> None:
    class FakeDatabase:
        async def readiness(self) -> str:
            return "test-revision"

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(database=FakeDatabase())
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["schema_revision"] == "test-revision"


def test_start_run_returns_an_accepted_queue_record() -> None:
    class FakeRuns:
        async def queue(self, agent_id: str, agent_input: str, trace_id: str) -> AgentRunAccepted:
            assert agent_input == "Research this"
            return AgentRunAccepted(
                run_id="run-1",
                trace_id=trace_id,
                agent_id=agent_id,
                agent_version=2,
                status="queued",
            )

    class FakeAgents:
        async def get_agent(self, agent_id):
            return SimpleNamespace(id=agent_id, version_id="agent-version-2")

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        runs=FakeRuns(), agents=FakeAgents()
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/agents/agent-1/runs", json={"input": "Research this"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]


def test_unknown_run_trajectory_returns_not_found() -> None:
    class FakeRuns:
        async def get(self, run_id: str):
            assert run_id == "missing-run"
            return None

        async def trajectory(self, run_id: str):
            assert run_id == "missing-run"
            return None

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(runs=FakeRuns())
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/runs/missing-run/trajectory")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def interruption_detail(status: WorkflowInterruptionStatus) -> WorkflowInterruptionDetail:
    return WorkflowInterruptionDetail(
        id="94d6048e-6e4c-4a8a-9069-bd10148b90aa",
        workflow_version_id="11850612-0402-418e-bee3-f4603a72d4eb",
        thread_id="3f0f3f50-37c8-481b-bd78-c3c6757a1f11",
        workflow_run_id="293ce849-ef4a-468b-ae41-21c92bcdd10e",
        node_id="approve",
        kind="approval",
        prompt="Approve publication?",
        response_schema={
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
            "additionalProperties": False,
        },
        checkpoint_id="checkpoint-1",
        status=status,
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
    )


def test_workflow_interruption_http_adapters_delegate_to_the_approval_service() -> None:
    interruption = interruption_detail(WorkflowInterruptionStatus.PENDING)

    class FakeApprovals:
        async def list(self, status):
            assert status == WorkflowInterruptionStatus.PENDING
            return [interruption]

        async def get(self, interruption_id):
            assert interruption_id == interruption.id
            return interruption

        async def respond(self, interruption_id, response, trace_id):
            assert interruption_id == interruption.id
            assert response == {"approved": True}
            assert trace_id
            return WorkflowRunAccepted(
                run_id="8f675d13-b826-407a-a82d-bcbe643756b4",
                trace_id=trace_id,
                workflow_id="cfdf2142-76c3-4bba-939c-b61a4cfd4932",
                workflow_version_id=interruption.workflow_version_id,
                workflow_version=2,
                status="queued",
                thread_id=interruption.thread_id,
                attempt_index=2,
                resumed_from_run_id=interruption.workflow_run_id,
                resume_checkpoint_id=interruption.checkpoint_id,
            )

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_approvals=FakeApprovals()
    )
    try:
        with TestClient(app) as client:
            listed = client.get("/api/v1/workflow-interruptions")
            fetched = client.get(f"/api/v1/workflow-interruptions/{interruption.id}")
            responded = client.post(
                f"/api/v1/workflow-interruptions/{interruption.id}/response",
                json={"approved": True},
            )
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "pending"
    assert fetched.status_code == 200
    assert fetched.json()["prompt"] == "Approve publication?"
    assert responded.status_code == 202
    assert responded.json()["attempt_index"] == 2
    assert responded.json()["trace_id"] == responded.headers["X-Trace-ID"]


def test_workflow_interruption_http_errors_are_safe_and_structured() -> None:
    resolved = interruption_detail(WorkflowInterruptionStatus.RESOLVED)

    class FakeApprovals:
        calls = 0

        async def get(self, _interruption_id):
            self.calls += 1
            return None if self.calls == 1 else resolved

        async def respond(self, _interruption_id, _response, _trace_id):
            raise ValueError("workflow interruption is not pending")

    interruption_id = "94d6048e-6e4c-4a8a-9069-bd10148b90aa"
    approvals = FakeApprovals()
    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_approvals=approvals
    )
    try:
        with TestClient(app) as client:
            missing = client.get(f"/api/v1/workflow-interruptions/{interruption_id}")
            conflict = client.post(
                f"/api/v1/workflow-interruptions/{interruption_id}/response",
                json={"approved": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert missing.json()["detail"] == "workflow interruption not found"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["message"] == "workflow interruption is not pending"


def test_workflow_run_stream_resumes_after_durable_event_cursor() -> None:
    run_id = UUID("293ce849-ef4a-468b-ae41-21c92bcdd10e")
    thread_id = UUID("3f0f3f50-37c8-481b-bd78-c3c6757a1f11")
    version_id = UUID("11850612-0402-418e-bee3-f4603a72d4eb")
    workflow_id = UUID("cfdf2142-76c3-4bba-939c-b61a4cfd4932")
    completed = WorkflowRunDetail(
        run_id=run_id,
        trace_id="trace-stream",
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        workflow_version=2,
        status="completed",
        thread_id=thread_id,
        input={},
        output={"result": "done"},
        attempt=1,
        queued_at=datetime(2026, 10, 7, tzinfo=UTC),
        completed_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    final_event = WorkflowRunEvent(
        id="c0306fd9-10be-412c-bca4-ac03f3a1fd1f",
        workflow_run_id=run_id,
        sequence=2,
        event_type="workflow.completed",
        data={"executed_nodes": ["output"]},
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
    )

    class FakeWorkflowRuns:
        async def get(self, received_run_id):
            assert received_run_id == run_id
            return completed

        async def event_batch(self, received_run_id, after_sequence=0):
            assert received_run_id == run_id
            assert after_sequence == 1
            return WorkflowRunEventBatch(
                status="completed",
                thread_id=thread_id,
                events=(final_event,),
            )

    class FakeWorkflows:
        async def get_version(self, received_version_id):
            assert received_version_id == version_id
            return SimpleNamespace(id=version_id)

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_runs=FakeWorkflowRuns(), workflows=FakeWorkflows()
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/workflow-runs/{run_id}/stream",
                headers={"Last-Event-ID": "1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 2\nevent: workflow.event\n" in response.text
    data_line = next(line for line in response.text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {
        "event_id": str(final_event.id),
        "run_id": str(run_id),
        "thread_id": str(thread_id),
        "sequence": 2,
        "event_type": "workflow.completed",
        "data": {"executed_nodes": ["output"]},
        "created_at": "2026-10-07T00:00:00+00:00",
    }


def test_unknown_workflow_run_stream_returns_not_found_before_streaming() -> None:
    class FakeWorkflowRuns:
        async def get(self, _run_id):
            return None

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_runs=FakeWorkflowRuns()
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/workflow-runs/293ce849-ef4a-468b-ae41-21c92bcdd10e/stream"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "workflow run not found"


def test_invalid_workflow_input_is_rejected_synchronously() -> None:
    workflow_id = UUID("47d174a8-b35e-4563-bd86-3bc6b5b5947f")
    version_id = UUID("c1a19ef5-eb93-4723-a720-c83371bef05f")

    class FakeWorkflowRuns:
        async def queue(self, received_version_id, workflow_input, trace_id):
            assert received_version_id == version_id
            assert workflow_input == {}
            assert trace_id
            raise WorkflowExecutionError(
                WorkflowExecutionIssue(
                    code="workflow.execution.invalid_input",
                    message="Workflow input is invalid: 'request' is a required property",
                    workflow_id=workflow_id,
                )
            )

    class FakeWorkflows:
        async def get_version(self, received_version_id):
            assert received_version_id == version_id
            return SimpleNamespace(id=version_id)

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_runs=FakeWorkflowRuns(), workflows=FakeWorkflows()
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/workflow-versions/{version_id}/runs",
                json={"input": {}},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["message"].startswith("Workflow input is invalid")


def test_webhook_endpoint_queues_the_external_json_event() -> None:
    trigger_id = UUID("07e8280f-0516-48f7-86dd-4828554f9d1a")
    trigger_event_id = UUID("ee1fd6d5-83f8-4c51-95bc-bb44c047c9d5")

    class FakeWebhooks:
        async def invoke(self, received_trigger_id, payload, trace_id):
            assert received_trigger_id == trigger_id
            assert payload == {"request": "external event"}
            return WorkflowRunAccepted(
                run_id="4df483b2-92c3-492f-92fc-1302e752e861",
                trace_id=trace_id,
                workflow_id="47d174a8-b35e-4563-bd86-3bc6b5b5947f",
                workflow_version_id="c1a19ef5-eb93-4723-a720-c83371bef05f",
                workflow_version=1,
                status="queued",
                trigger_id=trigger_id,
                trigger_type="webhook",
                trigger_event_id=trigger_event_id,
            )

    class FakeTriggers:
        async def get(self, received_trigger_id):
            assert received_trigger_id == trigger_id
            return SimpleNamespace(
                target_type="workflow",
                target_version_id=UUID("c1a19ef5-eb93-4723-a720-c83371bef05f"),
            )

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        webhook_triggers=FakeWebhooks(), triggers=FakeTriggers()
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/hooks/{trigger_id}",
                json={"request": "external event"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["trigger_event_id"] == str(trigger_event_id)
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]


def test_workflow_capabilities_report_runtime_support() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/workflows/capabilities")

    assert response.status_code == 200
    nodes = {node["type"]: node for node in response.json()["nodes"]}
    assert nodes["agent"]["available"] is True
    assert nodes["supervisor"]["available"] is True
    assert "tool" not in nodes
    assert nodes["connector"]["available"] is True
    assert "properties" in nodes["agent"]["config_schema"]


def test_tools_endpoint_exposes_dynamic_capability_provenance() -> None:
    registry = ToolRegistry()
    namespaced_tool = get_weather.model_copy(update={"name": "weather__get_weather"})
    registry.register(
        RegisteredCapability(
            id="weather__get_weather",
            tool=namespaced_tool,
            source="mcp",
            origin="weather",
        )
    )
    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        tools=registry,
        mcp_tools=SimpleNamespace(statuses=()),
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/tools")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["tools"] == ["weather__get_weather"]
    capability = body["capabilities"][0]
    assert capability | {"input_schema": None, "schema_fingerprint": None} == {
        "id": "weather__get_weather",
        "source": "mcp",
        "origin": "weather",
        "description": "Return test weather for one city.",
        "input_schema": None,
        "output_schema": None,
        "annotations": {},
        "schema_fingerprint": None,
    }
    assert capability["input_schema"]["properties"]["city"]["type"] == "string"
    assert capability["input_schema"]["required"] == ["city"]
    assert len(capability["schema_fingerprint"]) == 64
    assert body["mcp_servers"] == []


def test_workflow_validation_returns_structured_semantic_issues() -> None:
    runtime = SimpleNamespace(
        workflow_validator=WorkflowValidator(["calculator"], ["test"]),
    )
    workflow = {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Invalid workflow",
        "description": "Missing output.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [{"id": "input", "type": "input", "name": "Input", "config": {}}],
        "edges": [],
    }
    app.dependency_overrides[get_application] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/workflows/validate", json=workflow)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert {issue["code"] for issue in response.json()["issues"]} == {
        "workflow.node.no_output_path",
        "workflow.output.missing",
    }


def test_workflow_validation_accepts_editor_initial_workflow() -> None:
    runtime = SimpleNamespace(
        workflow_validator=WorkflowValidator(["calculator"], ["test"]),
    )
    workflow = {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "New workflow",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.request"},
            },
        ],
        "edges": [{"source": "input", "target": "output"}],
    }
    app.dependency_overrides[get_application] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/workflows/validate", json=workflow)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


def test_workflow_validation_returns_node_context_for_schema_issues() -> None:
    runtime = SimpleNamespace(
        workflow_validator=WorkflowValidator(["calculator"], ["test"]),
    )
    workflow = {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Invalid LLM",
        "description": "Node name is missing.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "entrypoint": "llm",
        "nodes": [
            {
                "id": "llm",
                "type": "agent",
                "config": {},
            }
        ],
        "edges": [],
    }
    app.dependency_overrides[get_application] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/workflows/validate", json=workflow)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    issue = response.json()["issues"][0]
    assert issue["code"] == "workflow.schema.invalid"
    assert issue["node_id"] == "llm"
    assert issue["field"].startswith("nodes.0")
