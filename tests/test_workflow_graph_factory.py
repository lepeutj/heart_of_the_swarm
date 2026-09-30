from copy import deepcopy
from typing import Any

import pytest
from langchain_core.tools import tool

from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def workflow_data(*, with_tool: bool = False, with_condition: bool = False) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "input", "type": "input", "name": "Input", "config": {}},
    ]
    edges: list[dict[str, Any]] = []
    previous = "input"
    if with_tool:
        nodes.append(
            {
                "id": "tool",
                "type": "tool",
                "name": "Tool",
                "config": {
                    "tool": "echo",
                    "arguments": {"value": {"from_state": "$.request"}},
                    "output_path": "$.result",
                },
            }
        )
        edges.append({"source": previous, "target": "tool"})
        previous = "tool"
    else:
        nodes.append(
            {
                "id": "copy",
                "type": "transform",
                "name": "Copy",
                "config": {"assign": {"$.result": {"from_state": "$.request"}}},
            }
        )
        edges.append({"source": previous, "target": "copy"})
        previous = "copy"

    if with_condition:
        nodes.extend(
            [
                {"id": "route", "type": "condition", "name": "Route", "config": {}},
                {
                    "id": "accepted",
                    "type": "output",
                    "name": "Accepted",
                    "config": {"output_path": "$.result"},
                },
                {
                    "id": "fallback",
                    "type": "output",
                    "name": "Fallback",
                    "config": {"output_path": "$.fallback"},
                },
            ]
        )
        edges.extend(
            [
                {"source": previous, "target": "route"},
                {
                    "source": "route",
                    "target": "accepted",
                    "condition": {
                        "path": "$.select",
                        "operator": "equals",
                        "value": True,
                    },
                },
                {"source": "route", "target": "fallback"},
            ]
        )
    else:
        nodes.append(
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.result"},
            }
        )
        edges.append({"source": previous, "target": "output"})

    return {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "LangGraph workflow",
        "description": "Exercise the LangGraph adapter.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": nodes,
        "edges": edges,
    }


def validate_workflow(data: dict[str, Any], tool_names: list[str] | None = None):
    spec = WorkflowSpec.model_validate(data)
    return WorkflowValidator(tool_names=tool_names or [], provider_names=[]).validate(spec)


def test_factory_rejects_unvalidated_workflow() -> None:
    spec = WorkflowSpec.model_validate(workflow_data())

    with pytest.raises(TypeError, match="requires a ValidatedWorkflowSpec"):
        WorkflowGraphFactory().create(spec)  # type: ignore[arg-type]


async def test_langgraph_executes_validated_sequential_workflow() -> None:
    workflow = validate_workflow(workflow_data())
    workflow_input = {"request": "hello"}
    original = deepcopy(workflow_input)

    graph = WorkflowGraphFactory().create(workflow)
    result = await graph.ainvoke(workflow_input)

    assert result.workflow_id == workflow.id
    assert result.output == "hello"
    assert result.state == {"request": "hello", "result": "hello"}
    assert result.executed_nodes == ("input", "copy", "output")
    assert workflow_input == original
    assert set(graph.graph.get_graph().nodes) == {
        "__start__",
        "workflow_node__input",
        "workflow_node__copy",
        "workflow_node__output",
        "__end__",
    }


async def test_factory_namespaces_node_ids_from_internal_state_channels() -> None:
    data = workflow_data()
    data["nodes"][1]["id"] = "data"
    data["edges"][0]["target"] = "data"
    data["edges"][1]["source"] = "data"
    workflow = validate_workflow(data)

    result = await WorkflowGraphFactory().create(workflow).ainvoke({"request": "hello"})

    assert result.output == "hello"
    assert result.executed_nodes == ("input", "data", "output")


async def test_langgraph_uses_validated_conditional_routes() -> None:
    workflow = validate_workflow(workflow_data(with_condition=True))
    graph = WorkflowGraphFactory().create(workflow)

    selected = await graph.ainvoke({"request": "selected", "select": True, "fallback": "fallback"})
    fallback = await graph.ainvoke({"request": "ignored", "select": False, "fallback": "fallback"})

    assert selected.output == "selected"
    assert selected.executed_nodes == ("input", "copy", "route", "accepted")
    assert fallback.output == "fallback"
    assert fallback.executed_nodes == ("input", "copy", "route", "fallback")


async def test_langgraph_invokes_registered_tool_once_and_records_trajectory() -> None:
    calls: list[str] = []

    @tool("echo")
    def echo(value: str) -> str:
        """Return one supplied value."""
        calls.append(value)
        return value

    workflow = validate_workflow(workflow_data(with_tool=True), ["echo"])
    callback = RuntimeCallbackHandler("workflow")
    graph = WorkflowGraphFactory(ToolRegistry([echo])).create(
        workflow,
        callback=callback,
        workflow_run_id="run-1",
    )

    result = await graph.ainvoke({"request": "hello"})

    assert result.output == "hello"
    assert calls == ["hello"]
    assert [event.event_type for event in callback.trajectory] == [
        "node.started",
        "node.completed",
        "node.started",
        "tool.started",
        "tool.completed",
        "node.completed",
        "node.started",
        "node.completed",
    ]


async def test_langgraph_preserves_structured_execution_failures() -> None:
    workflow = validate_workflow(workflow_data())
    graph = WorkflowGraphFactory().create(workflow)

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": 3})

    assert caught.value.issue.code == "workflow.execution.invalid_input"
    assert caught.value.issue.node_id == "input"
