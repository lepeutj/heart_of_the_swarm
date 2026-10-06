from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.workflows.conditions import ConditionSpec
from heart_of_the_swarm.workflows.configs import WorkflowNodeConfig
from heart_of_the_swarm.workflows.enums import NodeType


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64)
    type: NodeType
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64)
    target: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64)
    condition: ConditionSpec | None = None
    loop: "LoopSpec | None" = None
    label: str | None = Field(default=None, max_length=100)


class LoopSpec(BaseModel):
    """Bound one declared conditional back edge without introducing a loop node."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64)
    max_iterations: int = Field(ge=1, le=100)


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1", "2"]
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1_000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=200)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=500)
    entrypoint: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64)


class ValidatedWorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: NodeType
    name: str
    config: WorkflowNodeConfig


class ValidatedWorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1", "2"]
    id: UUID
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    nodes: list[ValidatedWorkflowNode]
    edges: list[WorkflowEdge]
    entrypoint: str
