import hashlib
import json
from pathlib import Path
from typing import Any

from heart_of_the_swarm.artifacts.models import (
    AgentArtifact,
    AgentArtifactManifest,
    AgentArtifactPayload,
    WorkflowArtifact,
    WorkflowArtifactManifest,
    WorkflowArtifactPayload,
)
from heart_of_the_swarm.workflows.configs import AgentNodeConfig, InlineAgentSource
from heart_of_the_swarm.workflows.enums import NodeType

MANIFEST_FILE = "manifest.json"
AGENT_FILE = "agent.json"
WORKFLOW_FILE = "workflow.json"


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


def workflow_payload_hash(payload: WorkflowArtifactPayload) -> str:
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


def load_workflow_artifact(directory: Path) -> WorkflowArtifact:
    """Load a workflow artifact and verify its identity and integrity metadata."""
    manifest = WorkflowArtifactManifest.model_validate_json(
        (directory / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    workflow = WorkflowArtifactPayload.model_validate_json(
        (directory / WORKFLOW_FILE).read_text(encoding="utf-8")
    )
    if workflow_payload_hash(workflow) != manifest.workflow_sha256:
        raise ValueError("workflow artifact hash does not match its manifest")
    if manifest.workflow_id != workflow.workflow_id:
        raise ValueError("workflow artifact contains inconsistent workflow IDs")
    if manifest.workflow_version_id != workflow.workflow_version_id:
        raise ValueError("workflow artifact contains inconsistent version IDs")
    if manifest.workflow_version != workflow.version:
        raise ValueError("workflow artifact contains inconsistent version numbers")
    if manifest.capabilities != workflow.capability_contracts:
        raise ValueError("workflow artifact contains inconsistent capability contracts")
    validate_workflow_portability(workflow)
    return WorkflowArtifact(manifest=manifest, workflow=workflow)


def validate_workflow_portability(workflow: WorkflowArtifactPayload) -> None:
    """Reject dependencies that the standalone V1 image cannot resolve by itself."""
    remote = [
        contract.capability_id
        for contract in workflow.capability_contracts
        if contract.source == "mcp"
    ]
    if remote:
        raise ValueError(
            "standalone workflow artifacts do not yet support MCP capabilities: "
            + ", ".join(sorted(remote))
        )
    for node in workflow.spec.nodes:
        if node.type == NodeType.SUBWORKFLOW:
            raise ValueError("standalone workflow artifacts do not yet bundle subworkflow versions")
        if node.type not in {NodeType.AGENT, NodeType.LLM}:
            continue
        config = AgentNodeConfig.model_validate(node.config)
        if not isinstance(config.source, InlineAgentSource):
            raise ValueError("standalone workflow artifacts require inline agent definitions")
        if config.source.agent.skills:
            raise ValueError("standalone workflow artifacts do not yet bundle agent Skills")
