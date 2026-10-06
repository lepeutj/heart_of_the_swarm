from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkflowInterruptionStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ApprovalResponse(BaseModel):
    """The complete V2.5a response contract accepted from an operator."""

    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool


APPROVAL_RESPONSE_SCHEMA = ApprovalResponse.model_json_schema()


class WorkflowInterruptionDetail(BaseModel):
    """Product record linking a human request to one durable runtime checkpoint."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workflow_version_id: UUID
    thread_id: UUID
    workflow_run_id: UUID
    node_id: str
    kind: Literal["approval"]
    prompt: str
    response_schema: dict[str, Any]
    checkpoint_id: str
    status: WorkflowInterruptionStatus
    response: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None
