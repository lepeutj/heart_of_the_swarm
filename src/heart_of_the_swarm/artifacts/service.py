import json
from pathlib import Path
from uuid import UUID

from heart_of_the_swarm import __version__
from heart_of_the_swarm.artifacts.loader import AGENT_FILE, MANIFEST_FILE, agent_payload_hash
from heart_of_the_swarm.artifacts.models import (
    AgentArtifact,
    AgentArtifactManifest,
    AgentArtifactPayload,
)
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import AgentRepository


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
