from copy import deepcopy
from typing import Any

import pytest
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


@tool
def increment(value: int) -> dict[str, int]:
    """Increment one integer for bounded-loop runtime tests."""
    return {"value": value + 1}


def workflow_data(*, with_condition: bool = False) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "input", "type": "input", "name": "Input", "config": {}},
    ]
    edges: list[dict[str, Any]] = []
    previous = "input"
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


def validate_workflow(data: dict[str, Any]):
    spec = WorkflowSpec.model_validate(data)
    return WorkflowValidator(tool_names=[], provider_names=[]).validate(spec)


def loop_workflow_data(*, exit_value: int = 3, max_iterations: int = 2) -> dict[str, Any]:
    return {
        "schema_version": "2",
        "id": "7d84d5ca-d4f9-4f2f-afec-75bd46110f20",
        "name": "Bounded loop",
        "description": "Increment state until the condition exits.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        "output_schema": {"type": "integer"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "increment",
                "type": "connector",
                "name": "Increment",
                "config": {
                    "capability_id": "increment",
                    "inputs": {"value": {"from_state": "$.value"}},
                    "outputs": {"value": {"to_state": "$.value"}},
                },
            },
            {"id": "route", "type": "condition", "name": "Done?", "config": {}},
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.value"},
            },
        ],
        "edges": [
            {"source": "input", "target": "increment"},
            {"source": "increment", "target": "route"},
            {
                "source": "route",
                "target": "output",
                "condition": {
                    "path": "$.value",
                    "operator": "greater_than",
                    "value": exit_value - 1,
                },
            },
            {
                "source": "route",
                "target": "increment",
                "loop": {"id": "increment", "max_iterations": max_iterations},
            },
        ],
    }


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


async def test_langgraph_preserves_structured_execution_failures() -> None:
    workflow = validate_workflow(workflow_data())
    graph = WorkflowGraphFactory().create(workflow)

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": 3})

    assert caught.value.issue.code == "workflow.execution.invalid_input"
    assert caught.value.issue.node_id == "input"


async def test_langgraph_executes_a_bounded_conditional_loop() -> None:
    tools = ToolRegistry([increment])
    spec = WorkflowSpec.model_validate(loop_workflow_data())
    workflow = WorkflowValidator(tool_names=tools.names, provider_names=[]).validate(spec)
    events: list[tuple[str, str]] = []

    async def record_event(event_type: str, node: Any, _payload: dict[str, Any]) -> None:
        events.append((event_type, node.id))

    graph = WorkflowGraphFactory(capabilities=tools).create(
        workflow,
        event_sink=record_event,
    )

    result = await graph.ainvoke({"value": 0}, recursion_limit=30)

    assert result.output == 3
    assert result.loop_iterations == {"increment": 2}
    assert result.executed_nodes == (
        "input",
        "increment",
        "route",
        "increment",
        "route",
        "increment",
        "route",
        "output",
    )
    assert [node_id for event, node_id in events if event == "node.completed"] == list(
        result.executed_nodes
    )


async def test_langgraph_rejects_a_loop_beyond_its_declared_limit() -> None:
    tools = ToolRegistry([increment])
    spec = WorkflowSpec.model_validate(loop_workflow_data(exit_value=100, max_iterations=2))
    workflow = WorkflowValidator(tool_names=tools.names, provider_names=[]).validate(spec)
    graph = WorkflowGraphFactory(capabilities=tools).create(workflow)

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"value": 0}, recursion_limit=30)

    assert caught.value.issue.code == "workflow.execution.iteration_limit"
    assert caught.value.issue.node_id == "route"


async def test_loop_counter_survives_checkpoint_resume() -> None:
    tools = ToolRegistry([increment])
    spec = WorkflowSpec.model_validate(loop_workflow_data())
    workflow = WorkflowValidator(tool_names=tools.names, provider_names=[]).validate(spec)
    saver = InMemorySaver()
    graph = WorkflowGraphFactory(capabilities=tools).create(workflow, checkpointer=saver)

    interrupted = await graph.ainvoke(
        {"value": 0},
        recursion_limit=30,
        thread_id="loop-thread",
        interrupt_after=("route",),
    )
    assert interrupted.interrupted is True
    assert interrupted.checkpoint_id is not None
    assert interrupted.loop_iterations == {"increment": 1}

    resumed = await graph.ainvoke(
        None,
        recursion_limit=30,
        thread_id="loop-thread",
        checkpoint_id=interrupted.checkpoint_id,
    )
    assert resumed.interrupted is False
    assert resumed.output == 3
    assert resumed.loop_iterations == {"increment": 2}
