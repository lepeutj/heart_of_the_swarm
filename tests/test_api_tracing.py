from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import tool

from heart_of_the_swarm import __version__
from heart_of_the_swarm.api import app, get_application
from heart_of_the_swarm.skills import SkillRegistry
from heart_of_the_swarm.spec import AgentRunAccepted
from heart_of_the_swarm.tools import RegisteredCapability, ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.runs import WorkflowRunAccepted


@asynccontextmanager
async def isolated_lifespan(_app):
    """Exercise API routes without starting PostgreSQL, MCP, or MLflow adapters."""
    yield


@pytest.fixture(autouse=True)
def isolate_application_lifespan():
    original = app.router.lifespan_context
    app.router.lifespan_context = isolated_lifespan
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

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(runs=FakeRuns())
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

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        workflow_runs=FakeWorkflowRuns()
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

    app.dependency_overrides[get_application] = lambda: SimpleNamespace(
        webhook_triggers=FakeWebhooks()
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
