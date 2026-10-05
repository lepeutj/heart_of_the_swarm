from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest

from heart_of_the_swarm.workflows import (
    NodeType,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def validate_workflow(data: dict[str, Any]):
    spec = WorkflowSpec.model_validate(data)
    return WorkflowValidator(tool_names=[], provider_names=[]).validate(spec)


def sequential_data() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "id": "4fc4fba0-f38c-4b03-95a4-d478f4636273",
        "name": "Copy request",
        "description": "Copy the request to the result.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "copy",
                "type": "transform",
                "name": "Copy",
                "config": {"assign": {"$.result": {"from_state": "$.request"}}},
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.result"},
            },
        ],
        "edges": [
            {"source": "input", "target": "copy"},
            {"source": "copy", "target": "output"},
        ],
    }


def conditional_data() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "id": "44d9df8d-08b1-4e41-81c0-c104b84e0679",
        "name": "Select result",
        "description": "Select the first matching output.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {"id": "route", "type": "condition", "name": "Route", "config": {}},
            {
                "id": "first",
                "type": "output",
                "name": "First",
                "config": {"output_path": "$.first"},
            },
            {
                "id": "second",
                "type": "output",
                "name": "Second",
                "config": {"output_path": "$.second"},
            },
            {
                "id": "fallback",
                "type": "output",
                "name": "Fallback",
                "config": {"output_path": "$.fallback"},
            },
        ],
        "edges": [
            {"source": "input", "target": "route"},
            {
                "source": "route",
                "target": "first",
                "condition": {"path": "$.score", "operator": "greater_than", "value": 0},
            },
            {
                "source": "route",
                "target": "second",
                "condition": {"path": "$.score", "operator": "less_than", "value": 10},
            },
            {"source": "route", "target": "fallback"},
        ],
    }


async def execute(data: dict[str, Any], workflow_input: dict[str, Any]):
    workflow = validate_workflow(data)
    return await WorkflowGraphFactory().create(workflow).ainvoke(workflow_input)


async def test_executes_sequential_workflow_without_mutating_input() -> None:
    workflow_input = {"request": "hello"}
    original = deepcopy(workflow_input)

    result = await execute(sequential_data(), workflow_input)

    assert result.workflow_id == UUID("4fc4fba0-f38c-4b03-95a4-d478f4636273")
    assert result.output == "hello"
    assert result.state == {"request": "hello", "result": "hello"}
    assert result.executed_nodes == ("input", "copy", "output")
    assert workflow_input == original


async def test_transform_assignments_resolve_from_one_state_snapshot() -> None:
    data = sequential_data()
    data["input_schema"] = {
        "type": "object",
        "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
        "required": ["left", "right"],
    }
    data["nodes"][1]["config"] = {
        "assign": {
            "$.left": {"from_state": "$.right"},
            "$.right": {"from_state": "$.left"},
        }
    }
    data["nodes"][2]["config"] = {"output_path": "$.left"}
    data["output_schema"] = {"type": "integer"}

    result = await execute(data, {"left": 1, "right": 2})

    assert result.output == 2
    assert result.state == {"left": 2, "right": 1}


async def test_condition_uses_first_matching_route() -> None:
    result = await execute(
        conditional_data(),
        {"score": 5, "first": "first", "second": "second", "fallback": "fallback"},
    )

    assert result.output == "first"
    assert result.executed_nodes == ("input", "route", "first")


async def test_condition_uses_fallback_when_no_route_matches() -> None:
    data = conditional_data()
    data["edges"][1]["condition"]["value"] = 100
    data["edges"][2]["condition"]["value"] = 0

    result = await execute(
        data,
        {"score": 5, "first": "first", "second": "second", "fallback": "fallback"},
    )

    assert result.output == "fallback"
    assert result.executed_nodes == ("input", "route", "fallback")


async def test_unselected_branch_is_not_executed() -> None:
    data = conditional_data()
    data["nodes"][3] = {
        "id": "second",
        "type": "transform",
        "name": "Broken unselected branch",
        "config": {"assign": {"$.second": {"from_state": "$.missing"}}},
    }
    data["edges"].append({"source": "second", "target": "fallback"})

    result = await execute(
        data,
        {"score": 5, "first": "selected", "fallback": "fallback"},
    )

    assert result.output == "selected"
    assert "second" not in result.executed_nodes


async def test_input_and_output_schema_failures_have_node_context() -> None:
    graph = WorkflowGraphFactory().create(validate_workflow(sequential_data()))
    with pytest.raises(WorkflowExecutionError) as invalid_input:
        await graph.ainvoke({"request": 3})
    assert invalid_input.value.issue.code == "workflow.execution.invalid_input"
    assert invalid_input.value.issue.node_id == "input"
    assert invalid_input.value.issue.node_type == NodeType.INPUT

    data = sequential_data()
    data["output_schema"] = {"type": "integer"}
    with pytest.raises(WorkflowExecutionError) as invalid_output:
        await execute(data, {"request": "not an integer"})
    assert invalid_output.value.issue.code == "workflow.execution.invalid_output"
    assert invalid_output.value.issue.node_id == "output"
    assert invalid_output.value.issue.node_type == NodeType.OUTPUT


async def test_transform_failure_does_not_mutate_external_input() -> None:
    data = sequential_data()
    data["nodes"][1]["config"] = {"assign": {"$.result": {"from_state": "$.missing"}}}
    workflow_input = {"request": "hello"}

    with pytest.raises(WorkflowExecutionError) as caught:
        await execute(data, workflow_input)

    assert caught.value.issue.code == "workflow.execution.transform_failed"
    assert caught.value.issue.node_id == "copy"
    assert caught.value.issue.node_type == NodeType.TRANSFORM
    assert workflow_input == {"request": "hello"}


async def test_condition_and_output_path_failures_have_node_context() -> None:
    with pytest.raises(WorkflowExecutionError) as condition_failure:
        await execute(
            conditional_data(),
            {"first": "first", "second": "second", "fallback": "fallback"},
        )
    assert condition_failure.value.issue.code == "workflow.execution.condition_failed"
    assert condition_failure.value.issue.node_id == "route"
    assert condition_failure.value.issue.node_type == NodeType.CONDITION

    data = sequential_data()
    data["nodes"][1]["config"] = {"assign": {"$.other": "value"}}
    with pytest.raises(WorkflowExecutionError) as output_failure:
        await execute(data, {"request": "hello"})
    assert output_failure.value.issue.code == "workflow.execution.output_missing"
    assert output_failure.value.issue.node_id == "output"
    assert output_failure.value.issue.node_type == NodeType.OUTPUT
