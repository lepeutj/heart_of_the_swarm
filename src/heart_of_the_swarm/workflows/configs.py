from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from heart_of_the_swarm.spec import ModelConfig
from heart_of_the_swarm.workflows.state import StatePath

_STATE_PATH_ADAPTER = TypeAdapter(StatePath)


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputNodeConfig(NodeConfig):
    pass


class AgentNodeConfig(NodeConfig):
    goal: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=4_000)
    model: ModelConfig
    tools: list[str] = Field(default_factory=list, max_length=20)
    input_path: StatePath
    output_path: StatePath

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, tools: list[str]) -> list[str]:
        if len(tools) != len(set(tools)):
            raise ValueError("tools must not contain duplicates")
        return tools


class LLMNodeConfig(NodeConfig):
    prompt: str = Field(min_length=1, max_length=10_000)
    model: ModelConfig
    input_path: StatePath
    output_path: StatePath


class ToolNodeConfig(NodeConfig):
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
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
    | ToolNodeConfig
    | ConditionNodeConfig
    | TransformNodeConfig
    | OutputNodeConfig
)
