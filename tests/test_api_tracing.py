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
