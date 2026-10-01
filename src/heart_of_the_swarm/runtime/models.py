from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.artifacts.models import AgentArtifactManifest


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=10_000)


class InvokeResponse(BaseModel):
    run_id: str
    trace_id: str
    output: str


class RuntimeMetadata(BaseModel):
    manifest: AgentArtifactManifest
    agent: dict[str, Any]
