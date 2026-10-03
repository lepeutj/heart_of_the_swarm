from typing import Any

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.artifacts.models import (
    AgentArtifactManifest,
    WorkflowArtifactManifest,
)


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Any


class InvokeResponse(BaseModel):
    run_id: str
    trace_id: str
    output: Any


class RuntimeMetadata(BaseModel):
    manifest: AgentArtifactManifest | WorkflowArtifactManifest
    agent: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
