from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
    WorkflowVersionRunner,
)
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot
from heart_of_the_swarm.workflows.execution import WorkflowExecutionEvent

PARENT_VERSION_ID = UUID("10000000-0000-4000-8000-000000000001")
CHILD_VERSION_ID = UUID("20000000-0000-4000-8000-000000000002")
PARENT_WORKFLOW_ID = UUID("30000000-0000-4000-8000-000000000003")
CHILD_WORKFLOW_ID = UUID("40000000-0000-4000-8000-000000000004")
MIDDLE_VERSION_ID = UUID("50000000-0000-4000-8000-000000000005")
MIDDLE_WORKFLOW_ID = UUID("60000000-0000-4000-8000-000000000006")

connector_calls: list[str] = []


@tool
async def child_fetch(value: str) -> dict[str, str]:
    """Return deterministic child data while recording external invocations."""
    connector_calls.append(value)
    return {"result": f"{value}|connector"}


def inline_agent(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "goal": "Process the mapped input",
        "instructions": "Return the processed value.",
        "model": {"provider": "test", "model_id": "test-model"},
        "tools": [],
    }


def child_spec() -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": str(CHILD_WORKFLOW_ID),
            "name": "Child research",
            "description": "Fetch and review isolated child data.",
            "input_schema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "fetch",
                    "type": "connector",
                    "name": "Fetch",
                    "config": {
                        "capability_id": "child_fetch",
                        "inputs": {"value": {"from_state": "$.question"}},
                        "outputs": {"result": {"to_state": "$.source"}},
                    },
                },
                {
                    "id": "review",
                    "type": "agent",
                    "name": "Review",
                    "config": {
                        "source": {"type": "inline", "agent": inline_agent("ChildReviewer")},
                        "input_path": "$.source",
                        "output_path": "$.summary",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"outputs": {"summary": {"from_state": "$.summary"}}},
                },
            ],
            "edges": [
                {"source": "input", "target": "fetch"},
                {"source": "fetch", "target": "review"},
                {"source": "review", "target": "output"},
            ],
        }
    )


def parent_spec(*, child_version_id: UUID = CHILD_VERSION_ID) -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "2",
            "id": str(PARENT_WORKFLOW_ID),
            "name": "Parent synthesis",
            "description": "Compose one immutable child workflow.",
            "input_schema": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "research",
                    "type": "subworkflow",
                    "name": "Research",
                    "config": {
                        "workflow_version_id": str(child_version_id),
                        "inputs": {"question": {"from_state": "$.request"}},
                        "outputs": {"summary": {"to_state": "$.research.summary"}},
                    },
                },
                {
                    "id": "finalize",
                    "type": "agent",
                    "name": "Finalize",
                    "config": {
                        "source": {"type": "inline", "agent": inline_agent("ParentWriter")},
                        "input_path": "$.research.summary",
                        "output_path": "$.final",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"outputs": {"result": {"from_state": "$.final"}}},
                },
            ],
            "edges": [
                {"source": "input", "target": "research"},
                {"source": "research", "target": "finalize"},
                {"source": "finalize", "target": "output"},
            ],
        }
    )


def parallel_parent_spec() -> WorkflowSpec:
    data = parent_spec().model_dump(mode="json")
    data["nodes"].insert(
        2,
        {
            "id": "context",
            "type": "transform",
            "name": "Context",
            "config": {"assign": {"$.context": "parallel"}},
        },
    )
    data["edges"] = [
        {"source": "input", "target": "research"},
        {"source": "input", "target": "context"},
        {"source": "research", "target": "finalize"},
        {"source": "context", "target": "finalize"},
        {"source": "finalize", "target": "output"},
    ]
    return WorkflowSpec.model_validate(data)


def snapshot(
    version_id: UUID,
    workflow_id: UUID,
    spec: WorkflowSpec,
    tools: ToolRegistry,
    *,
    with_connector: bool = False,
) -> WorkflowVersionSnapshot:
    return WorkflowVersionSnapshot(
        id=version_id,
        workflow_id=workflow_id,
        version=1,
        spec=spec,
        capability_contracts=(tools.contract("child_fetch"),) if with_connector else (),
    )


class MappingWorkflowVersionResolver:
    def __init__(self, versions: Mapping[UUID, WorkflowVersionSnapshot]) -> None:
        self.versions = versions
        self.calls: list[UUID] = []

    async def resolve(self, workflow_version_id: UUID) -> WorkflowVersionSnapshot | None:
        self.calls.append(workflow_version_id)
        return self.versions.get(workflow_version_id)


class ProcessingAgentRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        *,
        system_prompt: str | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        metadata: Mapping[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({"name": spec.name, "input": agent_input, "metadata": metadata})
        return f"{agent_input}|{spec.name}"


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[WorkflowExecutionEvent] = []

    async def emit(self, event: WorkflowExecutionEvent) -> None:
        self.events.append(event)


def runner(
    tools: ToolRegistry,
    resolver: MappingWorkflowVersionResolver,
    agents: ProcessingAgentRunner,
) -> WorkflowVersionRunner:
    validator = WorkflowValidator(lambda: tools.names, ["test"])
    return WorkflowVersionRunner(
        validator,
        tools,
        WorkflowGraphFactory(
            agent_runner=agents,  # type: ignore[arg-type]
            workflow_versions=resolver,
            validator=validator,
            capabilities=tools,
        ),
    )


async def test_subworkflow_executes_with_isolated_mappings_and_hierarchical_events() -> None:
    connector_calls.clear()
    tools = ToolRegistry([child_fetch])
    child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    resolver = MappingWorkflowVersionResolver({CHILD_VERSION_ID: child})
    agents = ProcessingAgentRunner()
    sink = RecordingSink()

    result = await runner(tools, resolver, agents).run(
        snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, parent_spec(), tools),
        {"request": "topic"},
        ExecutionPolicy(timeout_seconds=2, recursion_limit=50),
        sink,
        execution_id="run-1",
    )

    assert result.output == {"result": "topic|connector|ChildReviewer|ParentWriter"}
    assert connector_calls == ["topic"]
    assert [call["name"] for call in agents.calls] == ["ChildReviewer", "ParentWriter"]
    assert result.state["research"] == {"summary": "topic|connector|ChildReviewer"}
    assert "source" not in result.state
    completed_paths = [
        event.data["execution_path"]
        for event in sink.events
        if event.event_type == "node.completed"
    ]
    assert completed_paths == [
        "root/input",
        "root/research/input",
        "root/research/fetch",
        "root/research/review",
        "root/research/output",
        "root/research",
        "root/finalize",
        "root/output",
    ]
    child_events = [
        event
        for event in sink.events
        if str(event.data["execution_path"]).startswith("root/research/")
    ]
    assert {event.data["workflow_version_id"] for event in child_events} == {str(CHILD_VERSION_ID)}


async def test_subworkflow_executes_as_a_native_parallel_branch() -> None:
    connector_calls.clear()
    tools = ToolRegistry([child_fetch])
    child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    resolver = MappingWorkflowVersionResolver({CHILD_VERSION_ID: child})
    sink = RecordingSink()

    result = await runner(tools, resolver, ProcessingAgentRunner()).run(
        snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, parallel_parent_spec(), tools),
        {"request": "topic"},
        ExecutionPolicy(timeout_seconds=2, recursion_limit=50),
        sink,
    )

    assert result.output == {"result": "topic|connector|ChildReviewer|ParentWriter"}
    assert result.state["context"] == "parallel"
    research_event = next(
        event
        for event in sink.events
        if event.event_type == "node.completed" and event.data["execution_path"] == "root/research"
    )
    assert research_event.data["branch_id"] == "input:0"
    child_events = [
        event
        for event in sink.events
        if str(event.data["execution_path"]).startswith("root/research/")
    ]
    assert child_events
    assert {event.data["branch_id"] for event in child_events} == {"input:0"}


async def test_parallel_resume_inside_subworkflow_does_not_replay_sibling() -> None:
    connector_calls.clear()
    tools = ToolRegistry([child_fetch])
    child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    resolver = MappingWorkflowVersionResolver({CHILD_VERSION_ID: child})
    workflow_runner = runner(tools, resolver, ProcessingAgentRunner())
    parent = snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, parallel_parent_spec(), tools)
    checkpointer = InMemorySaver()
    sink = RecordingSink()
    policy = ExecutionPolicy(timeout_seconds=2, recursion_limit=50)

    interrupted = await workflow_runner.run(
        parent,
        {"request": "topic"},
        policy,
        sink,
        thread_id="parallel-subworkflow-thread",
        checkpointer=checkpointer,
        interrupt_after=("research/fetch",),
    )

    assert interrupted.interrupted is True
    assert connector_calls == ["topic"]
    assert [
        event.data["execution_path"]
        for event in sink.events
        if event.event_type == "node.completed" and event.data["execution_path"] == "root/context"
    ] == ["root/context"]

    resumed = await workflow_runner.run(
        parent,
        {"request": "ignored"},
        policy,
        sink,
        thread_id="parallel-subworkflow-thread",
        checkpoint_id=interrupted.checkpoint_id,
        checkpointer=checkpointer,
    )

    assert resumed.output == {"result": "topic|connector|ChildReviewer|ParentWriter"}
    assert connector_calls == ["topic"]
    assert (
        sum(
            event.event_type == "node.completed" and event.data["execution_path"] == "root/context"
            for event in sink.events
        )
        == 1
    )


async def test_resume_inside_subworkflow_does_not_replay_child_side_effects() -> None:
    connector_calls.clear()
    tools = ToolRegistry([child_fetch])
    child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    resolver = MappingWorkflowVersionResolver({CHILD_VERSION_ID: child})
    workflow_runner = runner(tools, resolver, ProcessingAgentRunner())
    parent = snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, parent_spec(), tools)
    checkpointer = InMemorySaver()
    policy = ExecutionPolicy(timeout_seconds=2, recursion_limit=50)

    interrupted = await workflow_runner.run(
        parent,
        {"request": "topic"},
        policy,
        thread_id="subworkflow-thread",
        checkpointer=checkpointer,
        interrupt_after=("research/fetch",),
    )
    assert interrupted.interrupted is True
    assert connector_calls == ["topic"]

    resumed = await workflow_runner.run(
        parent,
        {"request": "ignored"},
        policy,
        thread_id="subworkflow-thread",
        checkpoint_id=interrupted.checkpoint_id,
        checkpointer=checkpointer,
    )
    assert resumed.output == {"result": "topic|connector|ChildReviewer|ParentWriter"}
    assert connector_calls == ["topic"]


async def test_subworkflow_rejects_invalid_mapping_and_recursive_dependency() -> None:
    tools = ToolRegistry([child_fetch])
    child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    agents = ProcessingAgentRunner()
    invalid = parent_spec().model_copy(deep=True)
    invalid.nodes[1].config["inputs"] = {}
    invalid_parent = snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, invalid, tools)

    with pytest.raises(WorkflowExecutionError) as mapping_error:
        await runner(
            tools,
            MappingWorkflowVersionResolver({CHILD_VERSION_ID: child}),
            agents,
        ).run(
            invalid_parent,
            {"request": "topic"},
            ExecutionPolicy(timeout_seconds=2, recursion_limit=50),
        )
    assert mapping_error.value.issue.code == "workflow.execution.subworkflow_mapping_invalid"

    recursive_data = child_spec().model_dump(mode="json")
    recursive_data["schema_version"] = "2"
    recursive_data["nodes"].insert(
        -1,
        parent_spec(child_version_id=PARENT_VERSION_ID).nodes[1].model_dump(mode="json"),
    )
    recursive_data["edges"] = [
        {"source": "input", "target": "fetch"},
        {"source": "fetch", "target": "review"},
        {"source": "review", "target": "research"},
        {"source": "research", "target": "output"},
    ]
    recursive_child_spec = WorkflowSpec.model_validate(recursive_data)
    recursive_child = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        recursive_child_spec,
        tools,
        with_connector=True,
    )
    resolver = MappingWorkflowVersionResolver(
        {
            CHILD_VERSION_ID: recursive_child,
            PARENT_VERSION_ID: snapshot(
                PARENT_VERSION_ID,
                PARENT_WORKFLOW_ID,
                parent_spec(),
                tools,
            ),
        }
    )
    with pytest.raises(WorkflowExecutionError) as recursion_error:
        await runner(tools, resolver, agents).run(
            snapshot(PARENT_VERSION_ID, PARENT_WORKFLOW_ID, parent_spec(), tools),
            {"request": "topic"},
            ExecutionPolicy(timeout_seconds=2, recursion_limit=50),
        )
    assert recursion_error.value.issue.code == "workflow.execution.subworkflow_recursion"


async def test_subworkflow_depth_is_bounded_by_execution_policy() -> None:
    tools = ToolRegistry([child_fetch])
    leaf = snapshot(
        CHILD_VERSION_ID,
        CHILD_WORKFLOW_ID,
        child_spec(),
        tools,
        with_connector=True,
    )
    middle_data = parent_spec(child_version_id=CHILD_VERSION_ID).model_dump(mode="json")
    middle_data["id"] = str(MIDDLE_WORKFLOW_ID)
    middle_data["input_schema"] = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }
    middle_data["output_schema"] = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    middle_data["nodes"][-1]["config"] = {"outputs": {"summary": {"from_state": "$.final"}}}
    middle = snapshot(
        MIDDLE_VERSION_ID,
        MIDDLE_WORKFLOW_ID,
        WorkflowSpec.model_validate(middle_data),
        tools,
    )
    top = snapshot(
        PARENT_VERSION_ID,
        PARENT_WORKFLOW_ID,
        parent_spec(child_version_id=MIDDLE_VERSION_ID),
        tools,
    )
    resolver = MappingWorkflowVersionResolver({MIDDLE_VERSION_ID: middle, CHILD_VERSION_ID: leaf})

    with pytest.raises(WorkflowExecutionError) as depth_error:
        await runner(tools, resolver, ProcessingAgentRunner()).run(
            top,
            {"request": "topic"},
            ExecutionPolicy(
                timeout_seconds=2,
                recursion_limit=50,
                max_subworkflow_depth=1,
            ),
        )

    assert depth_error.value.issue.code == "workflow.execution.subworkflow_depth"
