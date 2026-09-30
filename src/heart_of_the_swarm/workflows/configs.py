from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from heart_of_the_swarm.spec import AgentSpec, ModelConfig
from heart_of_the_swarm.workflows.state import StatePath

_STATE_PATH_ADAPTER = TypeAdapter(StatePath)


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputNodeConfig(NodeConfig):
    pass


class InlineAgentSource(NodeConfig):
    type: Literal["inline"]
    spec: AgentSpec


class SavedAgentSource(NodeConfig):
    type: Literal["saved"]
    agent_version_id: UUID


AgentSource = Annotated[InlineAgentSource | SavedAgentSource, Field(discriminator="type")]


class AgentNodeConfig(NodeConfig):
    agent: AgentSource
    input_path: StatePath
    output_path: StatePath


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
