from typing import Annotated, Any, Literal
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
from heart_of_the_swarm.workflows.supervisors import (
    SUPERVISOR_DECISION_SCHEMA,
    SupervisorTargetId,
)

_STATE_PATH_ADAPTER = TypeAdapter(StatePath)
DataFieldName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=64),
]


def _validate_distinct_destinations(destinations: list[StatePath]) -> None:
    """Reject writes whose state paths are equal or nested inside one another."""
    for index, path in enumerate(destinations):
        for other in destinations[index + 1 :]:
            if path == other or path.startswith(f"{other}.") or other.startswith(f"{path}."):
                raise ValueError("output destinations must not overlap")


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
            _validate_distinct_destinations([binding.to_state for binding in self.outputs.values()])
        return self


class InlineAgentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inline"]
    agent: AgentSpec


class VersionedAgentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["version"]
    agent_version_id: UUID


class AgentNodeConfig(AgentDataFlowConfig):
    source: InlineAgentSource | VersionedAgentSource

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_source(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "source" in value:
            return value
        migrated = dict(value)
        if "agent" in migrated:
            migrated["source"] = {"type": "inline", "agent": migrated.pop("agent")}
        elif "agent_version_id" in migrated:
            migrated["source"] = {
                "type": "version",
                "agent_version_id": migrated.pop("agent_version_id"),
            }
        return migrated


class SupervisorTargetConfig(BaseModel):
    """Bind a dynamic decision task to one target agent's structured input."""

    model_config = ConfigDict(extra="forbid")

    task_field: DataFieldName = "task"


class SupervisorNodeConfig(NodeConfig):
    """Declare one bounded supervisor and the agent nodes it may select."""

    source: InlineAgentSource | VersionedAgentSource
    inputs: dict[DataFieldName, StateReference] = Field(min_length=1, max_length=50)
    allowed_targets: dict[SupervisorTargetId, SupervisorTargetConfig] = Field(
        min_length=1,
        max_length=20,
    )
    finish_output: OutputBinding
    decision_schema: str = Field(default=SUPERVISOR_DECISION_SCHEMA, max_length=64)
    max_handoffs_ref: Literal["execution_policy.max_handoffs"] = "execution_policy.max_handoffs"


class ConnectorNodeConfig(NodeConfig):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=100)
    inputs: dict[DataFieldName, Any] = Field(default_factory=dict, max_length=50)
    outputs: dict[DataFieldName, OutputBinding] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_outputs(self) -> "ConnectorNodeConfig":
        _validate_distinct_destinations([binding.to_state for binding in self.outputs.values()])
        return self


class SubworkflowNodeConfig(NodeConfig):
    """Map isolated parent state through one immutable child workflow version."""

    workflow_version_id: UUID
    inputs: dict[DataFieldName, StateReference] = Field(default_factory=dict, max_length=50)
    outputs: dict[DataFieldName, OutputBinding] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_outputs(self) -> "SubworkflowNodeConfig":
        _validate_distinct_destinations([binding.to_state for binding in self.outputs.values()])
        return self


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
    | SupervisorNodeConfig
    | ConnectorNodeConfig
    | SubworkflowNodeConfig
    | ConditionNodeConfig
    | TransformNodeConfig
    | OutputNodeConfig
)

NODE_CONFIG_TYPES: dict[NodeType, type[NodeConfig]] = {
    NodeType.INPUT: InputNodeConfig,
    NodeType.AGENT: AgentNodeConfig,
    NodeType.SUPERVISOR: SupervisorNodeConfig,
    NodeType.LLM: AgentNodeConfig,
    NodeType.CONNECTOR: ConnectorNodeConfig,
    NodeType.SUBWORKFLOW: SubworkflowNodeConfig,
    NodeType.CONDITION: ConditionNodeConfig,
    NodeType.TRANSFORM: TransformNodeConfig,
    NodeType.OUTPUT: OutputNodeConfig,
}
