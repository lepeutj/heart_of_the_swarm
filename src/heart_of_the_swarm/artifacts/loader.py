import hashlib
import json
from pathlib import Path
from typing import Any

from heart_of_the_swarm.artifacts.models import (
    AgentArtifact,
    AgentArtifactManifest,
    AgentArtifactPayload,
)

MANIFEST_FILE = "manifest.json"
AGENT_FILE = "agent.json"


def canonical_json(value: Any) -> bytes:
    """Return the stable JSON representation used for artifact integrity hashes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def agent_payload_hash(payload: AgentArtifactPayload) -> str:
    return hashlib.sha256(canonical_json(payload.model_dump(mode="json"))).hexdigest()


def load_agent_artifact(directory: Path) -> AgentArtifact:
    """Load an artifact and reject mismatched or corrupted declarations."""
    manifest = AgentArtifactManifest.model_validate_json(
        (directory / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    agent = AgentArtifactPayload.model_validate_json(
        (directory / AGENT_FILE).read_text(encoding="utf-8")
    )
    if agent_payload_hash(agent) != manifest.agent_sha256:
        raise ValueError("agent artifact hash does not match its manifest")
    if manifest.agent_id != agent.agent_id:
        raise ValueError("agent artifact contains inconsistent agent IDs")
    if manifest.agent_version_id != agent.agent_version_id:
        raise ValueError("agent artifact contains inconsistent version IDs")
    if manifest.agent_version != agent.version:
        raise ValueError("agent artifact contains inconsistent version numbers")
    if manifest.provider != agent.spec.model.provider:
        raise ValueError("agent artifact contains an inconsistent provider")
    if manifest.tools != tuple(agent.spec.tools):
        raise ValueError("agent artifact contains inconsistent tools")
    return AgentArtifact(manifest=manifest, agent=agent)
