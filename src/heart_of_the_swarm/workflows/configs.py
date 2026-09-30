from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.state import StatePath

_STATE_PATH_ADAPTER = TypeAdapter(StatePath)


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputNodeConfig(NodeConfig):
    pass


class AgentNodeConfig(NodeConfig):
    agent_version_id: UUID
    input_path: StatePath
    output_path: StatePath


class LLMNodeConfig(NodeConfig):
    agent: AgentSpec
    input_path: StatePath
    output_path: StatePath


class ConditionNodeConfig(NodeConfig):
    pass


class TransformNodeConfig(NodeConfig):
    assign: dict[str, Any] = Field(min_length=1, max_length=100)

    @field_validator("assign")
    @classmethod
    def valid_assignment_paths(cls, assignments: dict[str, Any]) -> dict[str, Any]:
        for path in assignments:
            _STATE_PATH_ADAPTER.validate_python(path)
        return assignments


class OutputNodeConfig(NodeConfig):
    output_path: StatePath


WorkflowNodeConfig = (
    InputNodeConfig
    | AgentNodeConfig
    | LLMNodeConfig
    | ConditionNodeConfig
    | TransformNodeConfig
    | OutputNodeConfig
)

NODE_CONFIG_TYPES: dict[NodeType, type[NodeConfig]] = {
    NodeType.INPUT: InputNodeConfig,
    NodeType.AGENT: AgentNodeConfig,
    NodeType.LLM: LLMNodeConfig,
    NodeType.CONDITION: ConditionNodeConfig,
    NodeType.TRANSFORM: TransformNodeConfig,
    NodeType.OUTPUT: OutputNodeConfig,
}
