from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    mlflow_trace_id: str | None = None
    output: Any


class RuntimeRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class RuntimeRunAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    trace_id: str
    status: RuntimeRunStatus


class RuntimeRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    event_type: str
    created_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class RuntimeRunDetail(RuntimeRunAccepted):
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    mlflow_trace_id: str | None = None
    result: Any = None
    error: str | None = None


class RuntimeObservability(BaseModel):
    mlflow: bool
    langchain_autolog: bool


class RuntimeMetadata(BaseModel):
    manifest: AgentArtifactManifest | WorkflowArtifactManifest
    runtime_id: str | None = None
    agent: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    observability: RuntimeObservability | None = None
