from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.workflows.configs import NODE_CONFIG_TYPES
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import WorkflowSpec
from heart_of_the_swarm.workflows.validation import WorkflowValidationIssue

_SUPPORTED_NODE_TYPES = {
    NodeType.INPUT,
    NodeType.AGENT,
    NodeType.SUPERVISOR,
    NodeType.HUMAN_APPROVAL,
    NodeType.CONNECTOR,
    NodeType.SUBWORKFLOW,
    NodeType.CONDITION,
    NodeType.TRANSFORM,
    NodeType.OUTPUT,
}

_NODE_DESCRIPTIONS = {
    NodeType.INPUT: "Validate and initialize workflow input.",
    NodeType.AGENT: "Run an inline AgentSpec or one immutable saved agent version.",
    NodeType.SUPERVISOR: "Route one task at a time to an explicitly allowed agent node.",
    NodeType.HUMAN_APPROVAL: "Pause for one durable boolean operator decision.",
    NodeType.LLM: "Deprecated inline-agent node; migrate it to AGENT.",
    NodeType.CONNECTOR: "Invoke one registered capability exactly once.",
    NodeType.SUBWORKFLOW: "Run one immutable workflow version with explicit state mappings.",
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

    schema_version: Literal["2"] = "2"
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
