import asyncio
from typing import Any

import pytest
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def parallel_execution_data(
    *,
    reducer: str = "append",
    state_type: str = "array",
) -> dict[str, Any]:
    return {
        "schema_version": "2",
        "id": "95826557-93ea-4495-bb76-d7028b319349",
        "name": "Parallel execution",
        "description": "Execute two connector branches and merge their results.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {"type": state_type},
        "state_schema": {
            "$.results": {"schema": {"type": state_type}, "reducer": reducer},
        },
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "left",
                "type": "connector",
                "name": "Left",
                "config": {
                    "capability_id": "left_branch",
                    "inputs": {"value": {"from_state": "$.request"}},
                    "outputs": {"result": {"to_state": "$.results"}},
                },
            },
            {
                "id": "right",
                "type": "connector",
                "name": "Right",
                "config": {
                    "capability_id": "right_branch",
                    "inputs": {"value": {"from_state": "$.request"}},
                    "outputs": {"result": {"to_state": "$.results"}},
                },
            },
            {
                "id": "join",
                "type": "transform",
                "name": "Join",
                "config": {"assign": {"$.final": {"from_state": "$.results"}}},
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.final"},
            },
        ],
        "edges": [
            {"source": "input", "target": "left"},
            {"source": "input", "target": "right"},
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "output"},
        ],
    }


def validate(data: dict[str, Any], tools: ToolRegistry):
    return WorkflowValidator(tools.names, []).validate(WorkflowSpec.model_validate(data))


def parallel_agent_data() -> dict[str, Any]:
    data = parallel_execution_data()
    for node, name in zip(data["nodes"][1:3], ("LeftAgent", "RightAgent"), strict=True):
        node["type"] = "agent"
        node["config"] = {
            "source": {
                "type": "inline",
                "agent": {
                    "name": name,
                    "goal": "Contribute one parallel result.",
                    "instructions": "Return the contribution.",
                    "model": {"provider": "test", "model_id": "test-model"},
                    "tools": [],
                    "skills": [],
                },
            },
            "input_path": "$.request",
            "output_path": "$.results",
        }
    return data


class ParallelAgentRunner:
    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        **kwargs: Any,
    ) -> list[str]:
        metadata = dict(kwargs.get("metadata") or {})
        self.calls.append((spec.name, metadata))
        if len(self.calls) == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        if spec.name == "LeftAgent":
            await asyncio.sleep(0.02)
        return [f"{spec.name}:{agent_input}"]


async def test_parallel_branches_overlap_and_append_in_declaration_order() -> None:
    ready = asyncio.Event()
    started: set[str] = set()

    async def wait_for_sibling(branch: str) -> None:
        started.add(branch)
        if len(started) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=1)

    @tool
    async def left_branch(value: str) -> dict[str, list[str]]:
        """Return the left test contribution after both branches start."""
        await wait_for_sibling("left")
        await asyncio.sleep(0.02)
        return {"result": [f"left:{value}"]}

    @tool
    async def right_branch(value: str) -> dict[str, list[str]]:
        """Return the right test contribution after both branches start."""
        await wait_for_sibling("right")
        return {"result": [f"right:{value}"]}

    tools = ToolRegistry([left_branch, right_branch])
    workflow = validate(parallel_execution_data(), tools)
    completed: list[str] = []
    branch_ids: dict[str, str] = {}

    async def record(event_type: str, node: Any, payload: dict[str, Any]) -> None:
        if event_type == "node.completed":
            completed.append(node.id)
            if "branch_id" in payload:
                branch_ids[node.id] = payload["branch_id"]

    result = (
        await WorkflowGraphFactory(capabilities=tools)
        .create(
            workflow,
            event_sink=record,
        )
        .ainvoke({"request": "topic"})
    )

    assert started == {"left", "right"}
    assert result.output == ["left:topic", "right:topic"]
    assert result.executed_nodes == ("input", "left", "right", "join", "output")
    assert completed.count("join") == 1
    assert branch_ids == {"left": "input:0", "right": "input:1"}


async def test_two_agents_execute_in_parallel_with_branch_context() -> None:
    agents = ParallelAgentRunner()
    workflow = WorkflowValidator([], ["test"]).validate(
        WorkflowSpec.model_validate(parallel_agent_data())
    )

    result = (
        await WorkflowGraphFactory(agent_runner=agents)
        .create(workflow)
        .ainvoke({"request": "topic"})
    )

    assert result.output == ["LeftAgent:topic", "RightAgent:topic"]
    assert {name: metadata["branch_id"] for name, metadata in agents.calls} == {
        "LeftAgent": "input:0",
        "RightAgent": "input:1",
    }


async def test_parallel_merge_dict_is_shallow_and_deterministic() -> None:
    @tool
    async def left_branch(value: str) -> dict[str, dict[str, str]]:
        """Return the left object contribution."""
        return {"result": {"left": value}}

    @tool
    async def right_branch(value: str) -> dict[str, dict[str, str]]:
        """Return the right object contribution."""
        return {"result": {"right": value}}

    tools = ToolRegistry([left_branch, right_branch])
    workflow = validate(
        parallel_execution_data(reducer="merge_dict", state_type="object"),
        tools,
    )

    result = (
        await WorkflowGraphFactory(capabilities=tools)
        .create(workflow)
        .ainvoke({"request": "topic"})
    )

    assert list(result.output) == ["left", "right"]
    assert result.output == {"left": "topic", "right": "topic"}


async def test_parallel_merge_dict_rejects_duplicate_runtime_keys() -> None:
    @tool
    async def left_branch(value: str) -> dict[str, dict[str, str]]:
        """Return one duplicate-key contribution."""
        return {"result": {"shared": f"left:{value}"}}

    @tool
    async def right_branch(value: str) -> dict[str, dict[str, str]]:
        """Return the other duplicate-key contribution."""
        return {"result": {"shared": f"right:{value}"}}

    tools = ToolRegistry([left_branch, right_branch])
    workflow = validate(
        parallel_execution_data(reducer="merge_dict", state_type="object"),
        tools,
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(capabilities=tools).create(workflow).ainvoke({"request": "topic"})
        )

    assert caught.value.issue.code == "workflow.execution.parallel_reduction_failed"
    assert caught.value.issue.node_id == "join"
    assert "duplicate keys: shared" in caught.value.issue.message


async def test_parallel_branch_failure_fails_the_workflow() -> None:
    @tool
    async def left_branch(value: str) -> dict[str, list[str]]:
        """Fail one parallel branch for execution propagation coverage."""
        raise RuntimeError(f"cannot process {value}")

    @tool
    async def right_branch(value: str) -> dict[str, list[str]]:
        """Return the healthy parallel branch contribution."""
        return {"result": [value]}

    tools = ToolRegistry([left_branch, right_branch])
    workflow = validate(parallel_execution_data(), tools)

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(capabilities=tools).create(workflow).ainvoke({"request": "topic"})
        )

    assert caught.value.issue.node_id == "left"


async def test_parallel_resume_does_not_replay_completed_sibling() -> None:
    calls = {"left": 0, "right": 0}

    @tool
    async def left_branch(value: str) -> dict[str, list[str]]:
        """Count and return the left checkpoint contribution."""
        calls["left"] += 1
        return {"result": [f"left:{value}"]}

    @tool
    async def right_branch(value: str) -> dict[str, list[str]]:
        """Count and return the right checkpoint contribution."""
        calls["right"] += 1
        return {"result": [f"right:{value}"]}

    tools = ToolRegistry([left_branch, right_branch])
    workflow = validate(parallel_execution_data(), tools)
    completed: list[str] = []

    async def record(event_type: str, node: Any, _payload: dict[str, Any]) -> None:
        if event_type == "node.completed":
            completed.append(node.id)

    graph = WorkflowGraphFactory(capabilities=tools).create(
        workflow,
        checkpointer=InMemorySaver(),
        event_sink=record,
    )
    interrupted = await graph.ainvoke(
        {"request": "topic"},
        thread_id="parallel-thread",
        interrupt_after=("right",),
    )

    assert interrupted.interrupted is True
    assert interrupted.checkpoint_id is not None
    assert calls == {"left": 1, "right": 1}

    resumed = await graph.ainvoke(
        None,
        thread_id="parallel-thread",
        checkpoint_id=interrupted.checkpoint_id,
    )

    assert resumed.interrupted is False
    assert resumed.output == ["left:topic", "right:topic"]
    assert calls == {"left": 1, "right": 1}
    assert completed.count("join") == 1
