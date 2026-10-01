from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.state import StatePath, StateReference

_STATE_PATH_ADAPTER = TypeAdapter(StatePath)
DataFieldName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=64),
]


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputNodeConfig(NodeConfig):
    pass


class OutputBinding(BaseModel):
    """Map one named node result field into shared workflow state."""

    model_config = ConfigDict(extra="forbid")

    to_state: StatePath


class AgentDataFlowConfig(NodeConfig):
    """Shared input/output mapping contract for inline and saved agents."""

    input_path: StatePath | None = None
    output_path: StatePath | None = None
    inputs: dict[DataFieldName, StateReference] | None = Field(
        default=None, min_length=1, max_length=50
    )
    outputs: dict[DataFieldName, OutputBinding] | None = Field(
        default=None, min_length=1, max_length=50
    )
    response_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def valid_data_flow(self) -> "AgentDataFlowConfig":
        if (self.input_path is None) == (self.inputs is None):
            raise ValueError("configure exactly one of input_path or inputs")
        if (self.output_path is None) == (self.outputs is None):
            raise ValueError("configure exactly one of output_path or outputs")
        if self.outputs is not None and len(self.outputs) > 1 and self.response_schema is None:
            raise ValueError("multiple outputs require response_schema")
        if self.outputs is not None:
            destinations = [binding.to_state for binding in self.outputs.values()]
            for index, path in enumerate(destinations):
                for other in destinations[index + 1 :]:
                    if (
                        path == other
                        or path.startswith(f"{other}.")
                        or other.startswith(f"{path}.")
                    ):
                        raise ValueError("output destinations must not overlap")
        return self


class AgentNodeConfig(AgentDataFlowConfig):
    agent_version_id: UUID


class LLMNodeConfig(AgentDataFlowConfig):
    agent: AgentSpec


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
    output_path: StatePath | None = None
    outputs: dict[DataFieldName, StateReference] | None = Field(
        default=None, min_length=1, max_length=50
    )

    @model_validator(mode="after")
    def valid_output(self) -> "OutputNodeConfig":
        if (self.output_path is None) == (self.outputs is None):
            raise ValueError("configure exactly one of output_path or outputs")
        return self


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
