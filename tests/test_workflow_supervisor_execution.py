import json
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_workflow_supervisor_validation import supervisor_workflow_data

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.workflows import (
    SupervisorDecision,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


class ScriptedAgentRunner:
    """Return supervisor decisions while recording delegated agent inputs."""

    def __init__(
        self,
        decisions: list[dict[str, Any]],
        *,
        failing_target: str | None = None,
    ) -> None:
        self.decisions = list(decisions)
        self.failing_target = failing_target
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        **kwargs: Any,
    ) -> Any:
        response_schema = kwargs.get("response_schema")
        self.calls.append((spec.name, agent_input, response_schema))
        if spec.name == "Supervisor":
            return self.decisions.pop(0)
        if spec.name == self.failing_target:
            raise RuntimeError("delegated agent failed")
        inputs = json.loads(agent_input)
        return f"{spec.name}: {inputs['task']}"


def validated_supervisor_workflow():
    data = supervisor_workflow_data()
    return WorkflowValidator([], ["test"]).validate(WorkflowSpec.model_validate(data))


async def test_supervisor_routes_two_agents_then_finishes() -> None:
    agents = ScriptedAgentRunner(
        [
            {"action": "handoff", "target": "researcher", "task": "Find sources."},
            {"action": "handoff", "target": "reviewer", "task": "Review findings."},
            {"action": "finish", "result": {"summary": "Complete"}},
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def record(event_type: str, _node: Any, payload: dict[str, Any]) -> None:
        events.append((event_type, payload))

    result = (
        await WorkflowGraphFactory(agent_runner=agents)
        .create(validated_supervisor_workflow(), event_sink=record)
        .ainvoke({"request": "AI safety"})
    )

    assert result.output == {"summary": "Complete"}
    assert "handoff_index" not in result.state
    assert "active_target" not in result.state
    assert [name for name, _, _ in agents.calls] == [
        "Supervisor",
        "Researcher",
        "Supervisor",
        "Reviewer",
        "Supervisor",
    ]
    assert [
        json.loads(agent_input)["task"]
        for name, agent_input, _ in agents.calls
        if name != "Supervisor"
    ] == [
        "Find sources.",
        "Review findings.",
    ]
    assert all(
        set(json.loads(agent_input)) == {"task", "topic"}
        for name, agent_input, _ in agents.calls
        if name != "Supervisor"
    )
    assert all(
        schema is SupervisorDecision for name, _, schema in agents.calls if name == "Supervisor"
    )
    assert [
        payload["handoff_index"] for event, payload in events if event == "handoff.started"
    ] == [1, 2]
    assert [
        payload["handoff_index"] for event, payload in events if event == "handoff.completed"
    ] == [1, 2]
    assert len([event for event, _ in events if event == "supervisor.decision"]) == 3


async def test_supervisor_rejects_model_selected_target_outside_allow_list() -> None:
    agents = ScriptedAgentRunner([{"action": "handoff", "target": "intruder", "task": "Run."}])

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(agent_runner=agents)
            .create(validated_supervisor_workflow())
            .ainvoke({"request": "topic"})
        )

    assert caught.value.issue.code == "workflow.execution.supervisor_target_not_allowed"


async def test_supervisor_handoff_limit_is_enforced_before_second_target_call() -> None:
    agents = ScriptedAgentRunner(
        [
            {"action": "handoff", "target": "researcher", "task": "First."},
            {"action": "handoff", "target": "reviewer", "task": "Second."},
        ]
    )
    events: list[str] = []

    async def record(event_type: str, _node: Any, _payload: dict[str, Any]) -> None:
        events.append(event_type)

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(agent_runner=agents)
            .create(
                validated_supervisor_workflow(),
                event_sink=record,
                max_handoffs=1,
            )
            .ainvoke({"request": "topic"})
        )

    assert caught.value.issue.code == "workflow.execution.handoff_limit_reached"
    assert [name for name, _, _ in agents.calls].count("Researcher") == 1
    assert [name for name, _, _ in agents.calls].count("Reviewer") == 0
    assert "handoff.limit_reached" in events


async def test_delegated_agent_failure_emits_handoff_failure() -> None:
    agents = ScriptedAgentRunner(
        [{"action": "handoff", "target": "researcher", "task": "Fail."}],
        failing_target="Researcher",
    )
    events: list[str] = []

    async def record(event_type: str, _node: Any, _payload: dict[str, Any]) -> None:
        events.append(event_type)

    with pytest.raises(WorkflowExecutionError):
        await (
            WorkflowGraphFactory(agent_runner=agents)
            .create(validated_supervisor_workflow(), event_sink=record)
            .ainvoke({"request": "topic"})
        )

    assert "handoff.failed" in events
    assert "handoff.completed" not in events


async def test_target_output_is_available_to_the_next_supervisor_decision() -> None:
    data = supervisor_workflow_data()
    data["nodes"][1]["config"]["inputs"]["research"] = {"from_state": "$.research"}
    data["nodes"][2]["config"]["output_path"] = "$.research"
    workflow = WorkflowValidator([], ["test"]).validate(WorkflowSpec.model_validate(data))
    agents = ScriptedAgentRunner(
        [
            {"action": "handoff", "target": "researcher", "task": "Find sources."},
            {"action": "finish", "result": "done"},
        ]
    )

    result = (
        await WorkflowGraphFactory(agent_runner=agents)
        .create(workflow)
        .ainvoke({"request": "topic", "research": None})
    )

    assert result.output == "done"
    second_supervisor_input = json.loads(agents.calls[2][1])
    assert second_supervisor_input["research"] == "Researcher: Find sources."


async def test_supervisor_can_select_the_same_target_more_than_once() -> None:
    agents = ScriptedAgentRunner(
        [
            {"action": "handoff", "target": "researcher", "task": "First pass."},
            {"action": "handoff", "target": "researcher", "task": "Second pass."},
            {"action": "finish", "result": "done"},
        ]
    )

    result = (
        await WorkflowGraphFactory(agent_runner=agents)
        .create(validated_supervisor_workflow())
        .ainvoke({"request": "topic"})
    )

    assert result.output == "done"
    assert [name for name, _, _ in agents.calls].count("Researcher") == 2


def test_graph_factory_rejects_invalid_handoff_bound() -> None:
    with pytest.raises(ValueError, match="max_handoffs"):
        WorkflowGraphFactory().create(validated_supervisor_workflow(), max_handoffs=0)


async def test_resume_after_delegated_agent_continues_at_supervisor() -> None:
    agents = ScriptedAgentRunner(
        [
            {"action": "handoff", "target": "researcher", "task": "Find sources."},
            {"action": "finish", "result": "done"},
        ]
    )
    events: list[str] = []

    async def record(event_type: str, _node: Any, _payload: dict[str, Any]) -> None:
        events.append(event_type)

    graph = WorkflowGraphFactory(agent_runner=agents).create(
        validated_supervisor_workflow(),
        checkpointer=InMemorySaver(),
        event_sink=record,
    )

    interrupted = await graph.ainvoke(
        {"request": "topic"},
        thread_id="supervisor-thread",
        interrupt_after=("researcher",),
    )

    assert interrupted.interrupted is True
    assert interrupted.checkpoint_id is not None
    assert [name for name, _, _ in agents.calls] == ["Supervisor", "Researcher"]
    assert "handoff.completed" not in events

    resumed = await graph.ainvoke(
        None,
        thread_id="supervisor-thread",
        checkpoint_id=interrupted.checkpoint_id,
    )

    assert resumed.output == "done"
    assert [name for name, _, _ in agents.calls] == [
        "Supervisor",
        "Researcher",
        "Supervisor",
    ]
    assert events.count("handoff.completed") == 1
    snapshot = await graph.graph.aget_state({"configurable": {"thread_id": "supervisor-thread"}})
    assert snapshot.values["handoff_index"] == 1
    assert snapshot.values["handoff_task"] is None
    assert snapshot.values["active_supervisor"] is None
    assert snapshot.values["active_target"] is None
