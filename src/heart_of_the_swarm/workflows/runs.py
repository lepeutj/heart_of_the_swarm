from datetime import datetime
from enum import StrEnum
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


class ExecutionThreadStatus(StrEnum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    CLOSED = "closed"


class WorkflowResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str = Field(min_length=1, max_length=100)


class WorkflowRunAccepted(BaseModel):
    run_id: UUID
    trace_id: str
    workflow_id: UUID
    workflow_version_id: UUID
    workflow_version: int
    status: RunStatus
    thread_id: UUID | None = None
    attempt_index: int = 1
    resumed_from_run_id: UUID | None = None
    resume_checkpoint_id: str | None = None
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
