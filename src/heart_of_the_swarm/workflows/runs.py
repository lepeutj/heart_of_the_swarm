from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.spec import RunStatus


class WorkflowRunRequest(BaseModel):
    """Structured input submitted to one immutable workflow version."""

    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunOrigin(BaseModel):
    """Metadata describing the trigger invocation that created a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: UUID
    trigger_type: str
    trigger_event_id: UUID


class WorkflowRunAccepted(BaseModel):
    run_id: UUID
    trace_id: str
    workflow_id: UUID
    workflow_version_id: UUID
    workflow_version: int
    status: RunStatus
    trigger_id: UUID | None = None
    trigger_type: str | None = None
    trigger_event_id: UUID | None = None


class WorkflowRunDetail(WorkflowRunAccepted):
    input: dict[str, Any]
    output: Any | None = None
    error: str | None = None
    attempt: int
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowRunEvent(BaseModel):
    id: UUID
    workflow_run_id: UUID
    sequence: int
    event_type: str
    data: dict[str, Any]
    created_at: datetime
