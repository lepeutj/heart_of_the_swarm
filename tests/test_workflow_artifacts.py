import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heart_of_the_swarm.artifacts import (
    WorkflowArtifactExporter,
    load_workflow_artifact,
)
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository
from heart_of_the_swarm.runtime.api import create_app
from heart_of_the_swarm.runtime.workflow import StandaloneWorkflowRuntime
from heart_of_the_swarm.tools import CapabilityContract, create_default_registry
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave


def calculator_workflow() -> WorkflowDraftSave:
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
                "name": "Standalone calculator",
                "description": "Invoke one built-in connector.",
                "input_schema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
                "output_schema": {"type": "string"},
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    {
                        "id": "calculate",
                        "type": "connector",
                        "name": "Calculate",
                        "config": {
                            "capability_id": "calculator",
                            "inputs": {
                                "expression": {"from_state": "$.expression"},
                            },
                            "outputs": {"result": {"to_state": "$.answer"}},
                        },
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"output_path": "$.answer"},
                    },
                ],
                "edges": [
                    {"source": "input", "target": "calculate"},
                    {"source": "calculate", "target": "output"},
                ],
            },
            "editor": {},
        }
    )


async def export_calculator_workflow(directory: Path):
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    tools = create_default_registry(Settings())
    try:
        async with database.session() as session:
            repository = WorkflowRepository(session)
            draft = await repository.save(calculator_workflow())
            version = await repository.create_version(
                str(draft.id),
                [tools.contract("calculator")],
                expected_revision=draft.revision,
            )
        assert version is not None
        artifact = await WorkflowArtifactExporter(database).export(version.id, directory)
        assert artifact is not None
        return artifact
    finally:
        await database.close()


async def test_workflow_artifact_is_complete_and_database_independent(tmp_path: Path) -> None:
    exported = await export_calculator_workflow(tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {"manifest.json", "workflow.json"}
    assert load_workflow_artifact(tmp_path) == exported
    serialized = (tmp_path / "workflow.json").read_text(encoding="utf-8")
    assert "database_url" not in serialized
    assert "api_key" not in serialized


async def test_workflow_artifact_loader_rejects_modified_payload(tmp_path: Path) -> None:
    await export_calculator_workflow(tmp_path)
    payload = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    payload["spec"]["name"] = "Modified after export"
    (tmp_path / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        load_workflow_artifact(tmp_path)


async def test_export_rejects_remote_capabilities_not_bundled_in_runtime(tmp_path: Path) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = WorkflowRepository(session)
            draft = await repository.save(calculator_workflow())
            version = await repository.create_version(
                str(draft.id),
                [
                    CapabilityContract(
                        capability_id="remote__calculator",
                        source="mcp",
                        origin="remote",
                        schema_fingerprint="0" * 64,
                    )
                ],
                expected_revision=draft.revision,
            )
        assert version is not None

        with pytest.raises(ValueError, match="do not yet support MCP"):
            await WorkflowArtifactExporter(database).export(version.id, tmp_path)
    finally:
        await database.close()


async def test_standalone_workflow_api_executes_artifact_without_database(
    tmp_path: Path,
) -> None:
    artifact = await export_calculator_workflow(tmp_path)
    settings = Settings(workflow_artifact_dir=tmp_path, mlflow_enabled=False)
    app = create_app(
        lambda: StandaloneWorkflowRuntime(settings),
        title="Test Workflow Runtime",
    )

    with TestClient(app) as client:
        health = client.get("/health")
        metadata = client.get("/metadata")
        invocation = client.post("/invoke", json={"input": {"expression": "2 + 3 * 4"}})

    assert health.json() == {"status": "ok"}
    assert metadata.json()["manifest"]["workflow_version_id"] == str(
        artifact.workflow.workflow_version_id
    )
    assert metadata.json()["workflow"]["name"] == "Standalone calculator"
    assert invocation.status_code == 200
    assert invocation.json()["output"] == "14"
    assert invocation.json()["run_id"]
    assert invocation.json()["trace_id"]
