import json
from pathlib import Path
from uuid import UUID

from heart_of_the_swarm import __version__
from heart_of_the_swarm.artifacts.loader import (
    AGENT_FILE,
    MANIFEST_FILE,
    WORKFLOW_FILE,
    agent_payload_hash,
    validate_workflow_portability,
    workflow_payload_hash,
)
from heart_of_the_swarm.artifacts.models import (
    AgentArtifact,
    AgentArtifactManifest,
    AgentArtifactPayload,
    WorkflowArtifact,
    WorkflowArtifactManifest,
    WorkflowArtifactPayload,
)
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import AgentRepository, WorkflowRepository


class AgentArtifactExporter:
    """Export one saved agent version into a database-independent directory."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def export(self, version_id: UUID, directory: Path) -> AgentArtifact | None:
        async with self.database.session() as session:
            version = await AgentRepository(session).get_version(str(version_id))
        if version is None:
            return None

        payload = AgentArtifactPayload(
            agent_id=version.id,
            agent_version_id=version.version_id,
            version=version.version,
            spec=version.spec,
            prompt_version=version.prompt_version,
            system_prompt=version.system_prompt,
        )
        manifest = AgentArtifactManifest(
            runtime_version=__version__,
            agent_id=payload.agent_id,
            agent_version_id=payload.agent_version_id,
            agent_version=payload.version,
            provider=payload.spec.model.provider,
            tools=tuple(payload.spec.tools),
            agent_sha256=agent_payload_hash(payload),
        )
        self._write(directory, AGENT_FILE, payload.model_dump(mode="json"))
        self._write(directory, MANIFEST_FILE, manifest.model_dump(mode="json"))
        return AgentArtifact(manifest=manifest, agent=payload)

    @staticmethod
    def _write(directory: Path, filename: str, value: dict[str, object]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        temporary = directory / f".{filename}.tmp"
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)


class WorkflowArtifactExporter:
    """Export one portable workflow version into a database-independent directory."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def export(self, version_id: UUID, directory: Path) -> WorkflowArtifact | None:
        async with self.database.session() as session:
            version = await WorkflowRepository(session).get_version(str(version_id))
        if version is None:
            return None

        payload = WorkflowArtifactPayload(
            workflow_id=version.workflow_id,
            workflow_version_id=version.id,
            version=version.version,
            spec=version.spec,
            capability_contracts=tuple(version.capability_contracts),
        )
        validate_workflow_portability(payload)
        manifest = WorkflowArtifactManifest(
            runtime_version=__version__,
            workflow_id=payload.workflow_id,
            workflow_version_id=payload.workflow_version_id,
            workflow_version=payload.version,
            capabilities=payload.capability_contracts,
            workflow_sha256=workflow_payload_hash(payload),
        )
        AgentArtifactExporter._write(directory, WORKFLOW_FILE, payload.model_dump(mode="json"))
        AgentArtifactExporter._write(directory, MANIFEST_FILE, manifest.model_dump(mode="json"))
        return WorkflowArtifact(manifest=manifest, workflow=payload)
