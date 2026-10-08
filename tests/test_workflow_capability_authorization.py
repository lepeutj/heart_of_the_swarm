from uuid import UUID

import pytest
from langchain_core.tools import tool

from heart_of_the_swarm.authorization import (
    AuthorizationService,
    ConfiguredCapabilityPolicySource,
)
from heart_of_the_swarm.capability_authorization import CapabilityAuthorizer
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import WorkflowGraphFactory, WorkflowSpec, WorkflowValidator
from heart_of_the_swarm.workflows.execution.errors import WorkflowExecutionError

WORKFLOW_VERSION_ID = UUID("ad2fe5d5-cb5e-4987-bc07-178339c85ec6")


def connector_workflow() -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "Authorized connector",
            "description": "Invoke one capability.",
            "input_schema": {"type": "object"},
            "output_schema": None,
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "fetch",
                    "type": "connector",
                    "name": "Fetch",
                    "config": {
                        "capability_id": "record_call",
                        "inputs": {"value": "ok"},
                        "outputs": {"result": {"to_state": "$.result"}},
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"outputs": {"result": {"from_state": "$.result"}}},
                },
            ],
            "edges": [
                {"source": "input", "target": "fetch"},
                {"source": "fetch", "target": "output"},
            ],
        }
    )


def graph(grants: dict[str, list[str]], calls: list[str]):
    @tool
    async def record_call(value: str) -> dict[str, str]:
        """Record one authorized side effect."""
        calls.append(value)
        return {"result": value}

    registry = ToolRegistry([record_call])
    workflow = WorkflowValidator(registry.names, []).validate(connector_workflow())
    authorizer = CapabilityAuthorizer(
        AuthorizationService(ConfiguredCapabilityPolicySource(grants))
    )
    return WorkflowGraphFactory(
        capabilities=registry,
        capability_authorizer=authorizer,
    ).create(workflow, workflow_version_id=str(WORKFLOW_VERSION_ID))


async def test_connector_allow_invokes_the_capability() -> None:
    calls: list[str] = []
    runtime = graph(
        {f"workflow_node:{WORKFLOW_VERSION_ID}/fetch": ["record_call"]},
        calls,
    )

    result = await runtime.ainvoke({})

    assert result.output == {"result": "ok"}
    assert calls == ["ok"]


async def test_connector_deny_has_no_capability_side_effect() -> None:
    calls: list[str] = []
    runtime = graph({}, calls)

    with pytest.raises(WorkflowExecutionError) as caught:
        await runtime.ainvoke({})

    assert caught.value.issue.code == "workflow.execution.connector_failed"
    assert calls == []
