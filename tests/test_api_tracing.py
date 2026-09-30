from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from heart_of_the_swarm import __version__
from heart_of_the_swarm.api import app, get_application
from heart_of_the_swarm.spec import AgentRunAccepted


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


def test_workflow_capabilities_report_runtime_support() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/workflows/capabilities")

    assert response.status_code == 200
    nodes = {node["type"]: node for node in response.json()["nodes"]}
    assert nodes["agent"]["available"] is True
    assert nodes["tool"]["available"] is True
    assert nodes["llm"]["available"] is False
    assert "properties" in nodes["agent"]["config_schema"]


def test_workflow_validation_returns_structured_semantic_issues() -> None:
    runtime = SimpleNamespace(
        tools=SimpleNamespace(names=("calculator",)),
        providers=SimpleNamespace(names=("test",)),
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
        tools=SimpleNamespace(names=("calculator",)),
        providers=SimpleNamespace(names=("test",)),
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
        tools=SimpleNamespace(names=("calculator",)),
        providers=SimpleNamespace(names=("test",)),
    )
    workflow = {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Invalid tool",
        "description": "Tool name is missing.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "entrypoint": "tool",
        "nodes": [
            {
                "id": "tool",
                "type": "tool",
                "config": {
                    "tool": "calculator",
                    "arguments": {},
                    "output_path": "$.result",
                },
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
    assert issue["node_id"] == "tool"
    assert issue["field"].startswith("nodes.0")
