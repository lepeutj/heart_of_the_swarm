from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import CapabilityContract
from heart_of_the_swarm.workflows.spec import WorkflowSpec


class AgentArtifactPayload(BaseModel):
    """Immutable agent data required by the standalone runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    agent_version_id: UUID
    version: int = Field(ge=1)
    spec: AgentSpec
    prompt_version: str
    system_prompt: str


class AgentArtifactManifest(BaseModel):
    """Integrity and compatibility metadata stored beside an agent payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["1"] = "1"
    runtime_version: str
    agent_id: UUID
    agent_version_id: UUID
    agent_version: int = Field(ge=1)
    provider: str
    tools: tuple[str, ...]
    agent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: AgentArtifactManifest
    agent: AgentArtifactPayload


class WorkflowArtifactPayload(BaseModel):
    """Immutable workflow data required by the standalone runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    workflow_version_id: UUID
    version: int = Field(ge=1)
    spec: WorkflowSpec
    capability_contracts: tuple[CapabilityContract, ...] = ()


class WorkflowArtifactManifest(BaseModel):
    """Integrity and compatibility metadata stored beside a workflow payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["1"] = "1"
    runtime_version: str
    workflow_id: UUID
    workflow_version_id: UUID
    workflow_version: int = Field(ge=1)
    capabilities: tuple[CapabilityContract, ...]
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: WorkflowArtifactManifest
    workflow: WorkflowArtifactPayload
