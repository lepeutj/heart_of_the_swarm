import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from heart_of_the_swarm.artifacts import AgentArtifactExporter
from heart_of_the_swarm.artifacts.models import AgentArtifactManifest
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.factory import render_system_prompt
from heart_of_the_swarm.repositories import AgentRepository
from heart_of_the_swarm.runtime.api import create_app
from heart_of_the_swarm.runtime.application import StandaloneAgentRuntime
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeMetadata
from heart_of_the_swarm.spec import AgentSpec


async def export_artifact(directory: Path, tools: list[str] | None = None):
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        spec = AgentSpec(
            name="DockerAgent",
            goal="Run independently",
            instructions="Answer clearly.",
            model={"provider": "openai", "model_id": "test-model"},
            tools=["calculator"] if tools is None else tools,
        )
        async with database.session() as session:
            saved = await AgentRepository(session).create(spec, "1", render_system_prompt(spec))
        artifact = await AgentArtifactExporter(database).export(UUID(saved.version_id), directory)
        assert artifact is not None
        return artifact
    finally:
        await database.close()


class RecordingRunner:
    def __init__(self) -> None:
        self.calls = []

    async def invoke(self, spec, agent_input, **kwargs):
        self.calls.append((spec, agent_input, kwargs))
        return "standalone answer"


class OpenAIStubHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.requests.append(json.loads(self.rfile.read(length)))
        body = json.dumps(
            {
                "id": "chatcmpl-runtime-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "standalone answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return None


@contextmanager
def openai_stub() -> Iterator[str]:
    OpenAIStubHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAIStubHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_runtime_loads_artifact_and_invokes_without_database(tmp_path: Path) -> None:
    artifact = await export_artifact(tmp_path)
    runtime = StandaloneAgentRuntime(
        Settings(openai_api_key="test-key", agent_artifact_dir=tmp_path)
    )
    runner = RecordingRunner()
    runtime.runner = runner  # type: ignore[assignment]

    await runtime.initialize()
    response = await runtime.invoke("Hello")

    assert response.output == "standalone answer"
    assert runtime.metadata().manifest == artifact.manifest
    assert runner.calls[0][1] == "Hello"
    assert runner.calls[0][2]["system_prompt"] == artifact.agent.system_prompt


def test_runtime_api_exposes_only_the_standard_contract() -> None:
    manifest = AgentArtifactManifest(
        runtime_version="test",
        agent_id="47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        agent_version_id="dc36aa94-3399-45d9-b285-e82787e8ca5d",
        agent_version=1,
        provider="openai",
        tools=(),
        agent_sha256="0" * 64,
    )

    class FakeRuntime:
        async def initialize(self) -> None:
            return None

        def metadata(self) -> RuntimeMetadata:
            return RuntimeMetadata(manifest=manifest, agent={"name": "Test"})

        async def invoke(self, agent_input: str) -> InvokeResponse:
            assert agent_input == "Hello"
            return InvokeResponse(run_id="run-1", trace_id="trace-1", output="Done")

    app = create_app(lambda: FakeRuntime())  # type: ignore[arg-type,return-value]
    with TestClient(app) as client:
        health = client.get("/health")
        metadata = client.get("/metadata")
        invocation = client.post("/invoke", json={"input": "Hello"})
        routes = {route.path for route in app.routes if hasattr(route, "path")}

    assert health.json() == {"status": "ok"}
    assert metadata.status_code == 200
    assert invocation.json() == {
        "run_id": "run-1",
        "trace_id": "trace-1",
        "output": "Done",
    }
    assert routes == {"/health", "/metadata", "/invoke"}


async def test_runtime_api_invokes_exported_agent_through_provider_protocol(tmp_path: Path) -> None:
    artifact = await export_artifact(tmp_path, tools=[])
    with openai_stub() as base_url:
        settings = Settings(
            openai_api_key="test-key",
            openai_base_url=base_url,
            agent_artifact_dir=tmp_path,
            mlflow_enabled=False,
        )
        app = create_app(lambda: StandaloneAgentRuntime(settings))
        with TestClient(app) as client:
            health = client.get("/health")
            metadata = client.get("/metadata")
            invocation = client.post("/invoke", json={"input": "Hello"})

    assert health.json() == {"status": "ok"}
    assert metadata.json()["manifest"]["agent_version_id"] == str(artifact.agent.agent_version_id)
    assert invocation.status_code == 200
    assert invocation.json()["output"] == "standalone answer"
    assert invocation.json()["run_id"]
    assert invocation.json()["trace_id"]
    assert OpenAIStubHandler.requests[0]["model"] == "test-model"
    assert OpenAIStubHandler.requests[0]["messages"][-1]["content"] == "Hello"
