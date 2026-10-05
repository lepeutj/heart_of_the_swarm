from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.tools import CapabilityContract
from heart_of_the_swarm.workflows.spec import WorkflowSpec


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class EditorViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, gt=0, le=4)


class WorkflowEditorDocument(BaseModel):
    """Non-executable React Flow presentation state stored beside a workflow draft."""

    model_config = ConfigDict(extra="forbid")

    positions: dict[str, NodePosition] = Field(default_factory=dict)
    viewport: EditorViewport = Field(default_factory=EditorViewport)


class WorkflowDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    editor: WorkflowEditorDocument = Field(default_factory=WorkflowEditorDocument)
    expected_revision: int | None = Field(default=None, ge=1)


class WorkflowSummary(BaseModel):
    id: UUID
    name: str
    description: str
    revision: int
    latest_version: int
    created_at: datetime
    updated_at: datetime


class WorkflowDraftDetail(WorkflowSummary):
    spec: dict[str, Any]
    editor: WorkflowEditorDocument


class WorkflowVersionSnapshot(BaseModel):
    """Persistence-independent data required to execute one immutable version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    workflow_id: UUID
    version: int
    spec: WorkflowSpec
    capability_contracts: tuple[CapabilityContract, ...] = ()


class WorkflowVersionDetail(WorkflowVersionSnapshot):
    editor: WorkflowEditorDocument
    created_at: datetime
