from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.workflows.conditions import ConditionSpec
from heart_of_the_swarm.workflows.configs import (
    ConditionNodeConfig,
    InputNodeConfig,
    OutputNodeConfig,
    ToolNodeConfig,
    TransformNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType


class CompiledRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str
    condition: ConditionSpec
    label: str | None = None


class CompiledNodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    dependencies: tuple[str, ...] = ()


class CompiledInputNode(CompiledNodeBase):
    type: Literal[NodeType.INPUT] = NodeType.INPUT
    config: InputNodeConfig
    next_node: str


class CompiledTransformNode(CompiledNodeBase):
    type: Literal[NodeType.TRANSFORM] = NodeType.TRANSFORM
    config: TransformNodeConfig
    next_node: str


class CompiledToolNode(CompiledNodeBase):
    type: Literal[NodeType.TOOL] = NodeType.TOOL
    config: ToolNodeConfig
    next_node: str


class CompiledConditionNode(CompiledNodeBase):
    type: Literal[NodeType.CONDITION] = NodeType.CONDITION
    config: ConditionNodeConfig
    routes: tuple[CompiledRoute, ...] = Field(min_length=1)
    fallback: str


class CompiledOutputNode(CompiledNodeBase):
    type: Literal[NodeType.OUTPUT] = NodeType.OUTPUT
    config: OutputNodeConfig


CompiledNode = Annotated[
    CompiledInputNode
    | CompiledTransformNode
    | CompiledToolNode
    | CompiledConditionNode
    | CompiledOutputNode,
    Field(discriminator="type"),
]


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"]
    workflow_id: UUID
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    entrypoint: str
    nodes: tuple[CompiledNode, ...]
    execution_order: tuple[str, ...]
