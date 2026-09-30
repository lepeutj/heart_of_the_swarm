from copy import deepcopy
from typing import Any

import pytest
from langchain_core.tools import tool

from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.tools.builtin import calculator
from heart_of_the_swarm.workflows import (
    NodeType,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def workflow_data(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    output_path: str = "$.tool_result",
    transform: bool = False,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "input", "type": "input", "name": "Input", "config": {}},
        {
            "id": "call_tool",
            "type": "tool",
            "name": "Call tool",
            "config": {
                "tool": tool_name,
                "arguments": arguments,
                "output_path": output_path,
            },
        },
    ]
    edges = [{"source": "input", "target": "call_tool"}]
    final_path = output_path
    previous = "call_tool"
    if transform:
        nodes.append(
            {
                "id": "prepare_output",
                "type": "transform",
                "name": "Prepare output",
                "config": {"assign": {"$.result": {"from_state": output_path}}},
            }
        )
        edges.append({"source": previous, "target": "prepare_output"})
        previous = "prepare_output"
        final_path = "$.result"
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "name": "Output",
            "config": {"output_path": final_path},
        }
    )
    edges.append({"source": previous, "target": "output"})
    return {
        "schema_version": "1",
        "id": "f14fd929-7a12-4394-9757-af72751d0fb6",
        "name": "Tool workflow",
        "description": "Invoke one registered tool.",
        "input_schema": {"type": "object"},
        "output_schema": {},
        "entrypoint": "input",
        "nodes": nodes,
        "edges": edges,
    }


def validate_tool_workflow(data: dict[str, Any], tool_names: list[str]):
    spec = WorkflowSpec.model_validate(data)
    return WorkflowValidator(tool_names=tool_names, provider_names=[]).validate(spec)


@pytest.mark.parametrize(
    ("arguments", "workflow_input", "expected"),
    [
        (
            {"query": "literal", "max_results": 2},
            {},
            {"query": "literal", "max_results": 2},
        ),
        (
            {
                "query": {"from_state": "$.request"},
                "max_results": {"from_state": "$.limit"},
            },
            {"request": "state", "limit": 3},
            {"query": "state", "max_results": 3},
        ),
        (
            {"query": {"from_state": "$.request"}, "max_results": 4},
            {"request": "mixed"},
            {"query": "mixed", "max_results": 4},
        ),
    ],
)
async def test_tool_arguments_support_literals_state_references_and_mixed_values(
    arguments: dict[str, Any],
    workflow_input: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    @tool("combine")
    def combine(query: str, max_results: int) -> dict[str, Any]:
        """Return the supplied arguments."""
        value = {"query": query, "max_results": max_results}
        calls.append(value)
        return value

    workflow = validate_tool_workflow(workflow_data("combine", arguments), ["combine"])
    result = (
        await WorkflowGraphFactory(ToolRegistry([combine])).create(workflow).ainvoke(workflow_input)
    )

    assert result.output == expected
    assert result.state["tool_result"] == expected
    assert calls == [expected]


async def test_calculator_tool_runs_once_then_transform_and_output() -> None:
    workflow = validate_tool_workflow(
        workflow_data(
            "calculator",
            {"expression": {"from_state": "$.expression"}},
            output_path="$.calculation",
            transform=True,
        ),
        ["calculator"],
    )

    result = (
        await WorkflowGraphFactory(ToolRegistry([calculator]))
        .create(workflow)
        .ainvoke({"expression": "(2 + 5) * 3"})
    )

    assert result.output == "21"
    assert result.state["calculation"] == "21"
    assert result.executed_nodes == ("input", "call_tool", "prepare_output", "output")


async def test_fake_web_search_tool_executes_without_network() -> None:
    calls: list[str] = []

    @tool("web_search")
    async def fake_web_search(query: str) -> str:
        """Return a deterministic fake search result."""
        calls.append(query)
        return f"result for {query}"

    workflow = validate_tool_workflow(
        workflow_data("web_search", {"query": {"from_state": "$.request"}}),
        ["web_search"],
    )

    result = (
        await WorkflowGraphFactory(ToolRegistry([fake_web_search]))
        .create(workflow)
        .ainvoke({"request": "agent architectures"})
    )

    assert result.output == "result for agent architectures"
    assert calls == ["agent architectures"]


async def test_unknown_tool_is_rejected_before_execution() -> None:
    workflow = validate_tool_workflow(workflow_data("missing_tool", {}), ["missing_tool"])

    with pytest.raises(WorkflowExecutionError) as caught:
        WorkflowGraphFactory(ToolRegistry([])).create(workflow)

    assert caught.value.issue.code == "workflow.execution.tool_unknown"
    assert caught.value.issue.node_id == "call_tool"
    assert caught.value.issue.node_type == NodeType.TOOL
    assert caught.value.issue.tool_name == "missing_tool"


async def test_missing_state_reference_does_not_invoke_tool() -> None:
    calls = 0

    @tool("counter")
    def counter(value: str) -> str:
        """Count deterministic invocations."""
        nonlocal calls
        calls += 1
        return value

    workflow = validate_tool_workflow(
        workflow_data("counter", {"value": {"from_state": "$.missing"}}),
        ["counter"],
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await WorkflowGraphFactory(ToolRegistry([counter])).create(workflow).ainvoke({})

    assert caught.value.issue.code == "workflow.execution.tool_arguments_unresolved"
    assert caught.value.issue.tool_name == "counter"
    assert calls == 0


async def test_invalid_tool_arguments_are_normalized_without_invocation() -> None:
    calls = 0

    @tool("typed_tool")
    def typed_tool(count: int) -> int:
        """Return a validated integer."""
        nonlocal calls
        calls += 1
        return count

    workflow = validate_tool_workflow(
        workflow_data("typed_tool", {"count": {"unexpected": "object"}}),
        ["typed_tool"],
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await WorkflowGraphFactory(ToolRegistry([typed_tool])).create(workflow).ainvoke({})

    assert caught.value.issue.code == "workflow.execution.tool_arguments_invalid"
    assert caught.value.issue.message == "Arguments for tool 'typed_tool' are invalid."
    assert calls == 0


@pytest.mark.parametrize("policy", ["handle_validation_error", "handle_tool_error"])
async def test_tool_error_swallowing_policies_are_rejected_before_execution(
    policy: str,
) -> None:
    calls = 0

    @tool("handled_tool")
    def handled_tool(count: int) -> int:
        """Return a validated integer."""
        nonlocal calls
        calls += 1
        return count

    setattr(handled_tool, policy, True)
    workflow = validate_tool_workflow(
        workflow_data("handled_tool", {"count": "invalid"}),
        ["handled_tool"],
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        WorkflowGraphFactory(ToolRegistry([handled_tool])).create(workflow)

    assert caught.value.issue.code == "workflow.execution.tool_error_policy_unsupported"
    assert caught.value.issue.tool_name == "handled_tool"
    assert calls == 0


async def test_tool_exception_is_safe_and_invoked_once() -> None:
    calls = 0
    callback = RuntimeCallbackHandler("workflow")

    @tool("failing_tool")
    def failing_tool() -> str:
        """Raise a deterministic test failure."""
        nonlocal calls
        calls += 1
        raise RuntimeError("private upstream detail")

    workflow = validate_tool_workflow(workflow_data("failing_tool", {}), ["failing_tool"])

    with pytest.raises(WorkflowExecutionError) as caught:
        graph = WorkflowGraphFactory(ToolRegistry([failing_tool])).create(
            workflow,
            callback=callback,
            workflow_run_id="workflow-run-failed",
        )
        await graph.ainvoke({})

    assert caught.value.issue.code == "workflow.execution.tool_failed"
    assert caught.value.issue.message == "Tool 'failing_tool' failed."
    assert caught.value.issue.tool_name == "failing_tool"
    assert "private upstream detail" not in caught.value.issue.message
    assert calls == 1
    assert [event.event_type for event in callback.trajectory][-3:] == [
        "tool.started",
        "tool.failed",
        "node.failed",
    ]
    tool_failed = callback.trajectory[-2]
    assert tool_failed.payload["workflow_run_id"] == "workflow-run-failed"
    assert tool_failed.payload["node_id"] == "call_tool"
    assert callback.trajectory[-1].payload["error_code"] == "workflow.execution.tool_failed"


async def test_uncopyable_tool_output_is_normalized_after_successful_invocation() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            raise RuntimeError("private copy detail")

    callback = RuntimeCallbackHandler("workflow")

    @tool("uncopyable")
    def uncopyable() -> Any:
        """Return a value that cannot be copied into workflow state."""
        return Uncopyable()

    workflow = validate_tool_workflow(workflow_data("uncopyable", {}), ["uncopyable"])

    with pytest.raises(WorkflowExecutionError) as caught:
        graph = WorkflowGraphFactory(ToolRegistry([uncopyable])).create(workflow, callback=callback)
        await graph.ainvoke({})

    assert caught.value.issue.code == "workflow.execution.tool_output_failed"
    assert caught.value.issue.message == (
        "Result from tool 'uncopyable' could not be written to state."
    )
    assert "private copy detail" not in caught.value.issue.message
    assert [event.event_type for event in callback.trajectory][-2:] == [
        "tool.completed",
        "node.failed",
    ]
    assert callback.trajectory[-1].payload["error_code"] == (
        "workflow.execution.tool_output_failed"
    )


async def test_tool_output_creates_nested_path_without_mutating_input() -> None:
    @tool("echo")
    def echo(value: str) -> str:
        """Return the supplied value."""
        return value

    workflow = validate_tool_workflow(
        workflow_data(
            "echo",
            {"value": {"from_state": "$.request"}},
            output_path="$.research.tool_result",
        ),
        ["echo"],
    )
    workflow_input = {"request": "hello"}
    original = deepcopy(workflow_input)

    result = (
        await WorkflowGraphFactory(ToolRegistry([echo])).create(workflow).ainvoke(workflow_input)
    )

    assert result.output == "hello"
    assert result.state["research"] == {"tool_result": "hello"}
    assert workflow_input == original


async def test_tool_and_node_events_use_existing_trajectory_callback() -> None:
    @tool("echo")
    def echo(value: str) -> str:
        """Return the supplied value."""
        return value

    workflow = validate_tool_workflow(
        workflow_data("echo", {"value": {"from_state": "$.request"}}),
        ["echo"],
    )
    callback = RuntimeCallbackHandler("workflow")

    graph = WorkflowGraphFactory(ToolRegistry([echo])).create(
        workflow,
        callback=callback,
        workflow_run_id="workflow-run-1",
    )
    result = await graph.ainvoke({"request": "hello"})

    assert result.output == "hello"
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
    tool_started = next(
        event for event in callback.trajectory if event.event_type == "tool.started"
    )
    assert tool_started.component == "echo"
    assert tool_started.payload["workflow_id"] == str(workflow.id)
    assert tool_started.payload["workflow_run_id"] == "workflow-run-1"
    assert tool_started.payload["node_id"] == "call_tool"
    assert tool_started.payload["node_type"] == "tool"
    assert tool_started.payload["input"] == {"value": "hello"}
    assert callback.events == []
