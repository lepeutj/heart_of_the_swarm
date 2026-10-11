import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from heart_of_the_swarm.artifacts import AgentArtifactExporter, load_agent_artifact
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.factory import render_system_prompt
from heart_of_the_swarm.repositories import AgentRepository
from heart_of_the_swarm.spec import AgentSpec


def agent_spec() -> AgentSpec:
    return AgentSpec(
        name="DockerAgent",
        goal="Run independently",
        instructions="Answer clearly.",
        model={"provider": "openai", "model_id": "test-model"},
        tools=["calculator"],
    )


async def export_artifact(directory: Path):
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        spec = agent_spec()
        async with database.session() as session:
            saved = await AgentRepository(session).create(spec, "1", render_system_prompt(spec))
        artifact = await AgentArtifactExporter(database).export(UUID(saved.version_id), directory)
        assert artifact is not None
        return artifact
    finally:
        await database.close()


async def test_exported_artifact_is_complete_and_database_independent(tmp_path: Path) -> None:
    exported = await export_artifact(tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {"agent.json", "manifest.json"}
    loaded = load_agent_artifact(tmp_path)
    assert loaded == exported
    assert loaded.manifest.provider == "openai"
    assert loaded.manifest.runtime_type == "agent"
    assert loaded.manifest.created_at is not None
    assert loaded.manifest.tools == ("calculator",)
    serialized = (tmp_path / "agent.json").read_text(encoding="utf-8")
    assert "api_key" not in serialized
    assert "database" not in serialized


async def test_loader_rejects_a_modified_agent_payload(tmp_path: Path) -> None:
    await export_artifact(tmp_path)
    payload = json.loads((tmp_path / "agent.json").read_text(encoding="utf-8"))
    payload["spec"]["goal"] = "Modified after export"
    (tmp_path / "agent.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        load_agent_artifact(tmp_path)


async def test_export_returns_none_for_unknown_version(tmp_path: Path) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        artifact = await AgentArtifactExporter(database).export(uuid4(), tmp_path)
    finally:
        await database.close()

    assert artifact is None
    assert list(tmp_path.iterdir()) == []


async def test_export_preserves_the_selected_historical_version(tmp_path: Path) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        first_spec = agent_spec()
        second_spec = first_spec.model_copy(update={"goal": "A newer behavior"})
        async with database.session() as session:
            first = await AgentRepository(session).create(
                first_spec,
                "1",
                render_system_prompt(first_spec),
            )
        async with database.session() as session:
            await AgentRepository(session).add_version(
                first.id,
                second_spec,
                "1",
                render_system_prompt(second_spec),
            )

        exported = await AgentArtifactExporter(database).export(UUID(first.version_id), tmp_path)
        loaded = load_agent_artifact(tmp_path)
    finally:
        await database.close()

    assert exported is not None
    assert loaded.agent.agent_version_id == UUID(first.version_id)
    assert loaded.agent.version == 1
    assert loaded.agent.spec.goal == "Run independently"
