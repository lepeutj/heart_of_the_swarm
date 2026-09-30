from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.workflows.configs import NODE_CONFIG_TYPES
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import WorkflowSpec
from heart_of_the_swarm.workflows.validation import WorkflowValidationIssue

_SUPPORTED_NODE_TYPES = {
    NodeType.INPUT,
    NodeType.AGENT,
    NodeType.TOOL,
    NodeType.CONDITION,
    NodeType.TRANSFORM,
    NodeType.OUTPUT,
}

_NODE_DESCRIPTIONS = {
    NodeType.INPUT: "Validate and initialize workflow input.",
    NodeType.AGENT: "Run one inline or saved LangChain agent.",
    NodeType.LLM: "Run one model call without tools.",
    NodeType.TOOL: "Invoke one registered tool exactly once.",
    NodeType.CONDITION: "Select one ordered route or its fallback.",
    NodeType.TRANSFORM: "Apply safe declarative state assignments.",
    NodeType.OUTPUT: "Resolve and validate the workflow result.",
}


class WorkflowNodeCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: NodeType
    available: bool
    description: str
    config_schema: dict[str, Any]


class WorkflowCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    workflow_schema: dict[str, Any]
    nodes: list[WorkflowNodeCapability]


class WorkflowValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[WorkflowValidationIssue]


def workflow_capabilities() -> WorkflowCapabilities:
    """Project backend workflow contracts into safe editor metadata."""
    return WorkflowCapabilities(
        workflow_schema=WorkflowSpec.model_json_schema(),
        nodes=[
            WorkflowNodeCapability(
                type=node_type,
                available=node_type in _SUPPORTED_NODE_TYPES,
                description=_NODE_DESCRIPTIONS[node_type],
                config_schema=NODE_CONFIG_TYPES[node_type].model_json_schema(),
            )
            for node_type in NodeType
        ],
    )
