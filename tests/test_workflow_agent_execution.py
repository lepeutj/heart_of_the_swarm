from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows import (
    ResolvedAgentVersion,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)

VERSION_ID = UUID("f5427628-42a7-4698-9e8c-7489da8a7a41")


def agent_spec() -> AgentSpec:
    return AgentSpec(
        name="ResearchAgent",
        goal="Research the request",
        instructions="Return a concise answer.",
        model={"provider": "test", "model_id": "test-model"},
        tools=[],
    )


def workflow_data(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Agent workflow",
        "description": "Execute one agent.",
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
                "id": "agent",
                "type": "agent",
                "name": "Agent",
                "config": {
                    "agent": source,
                    "input_path": "$.request",
                    "output_path": "$.answer",
                },
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.answer"},
            },
        ],
        "edges": [
            {"source": "input", "target": "agent"},
            {"source": "agent", "target": "output"},
        ],
    }


def validate(source: dict[str, Any]):
    spec = WorkflowSpec.model_validate(workflow_data(source))
    return WorkflowValidator(tool_names=[], provider_names=["test"]).validate(spec)


class RecordingAgentRunner:
    def __init__(self, output: str = "Answer", error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        *,
        system_prompt: str | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            {
                "spec": spec,
                "input": agent_input,
                "system_prompt": system_prompt,
                "callbacks": list(callbacks),
                "metadata": dict(metadata or {}),
            }
        )
        if self.error is not None:
            raise self.error
        return self.output


class FakeAgentVersionResolver:
    def __init__(self, resolved: ResolvedAgentVersion | None) -> None:
        self.resolved = resolved
        self.calls: list[UUID] = []

    async def resolve(self, agent_version_id: UUID) -> ResolvedAgentVersion | None:
        self.calls.append(agent_version_id)
        return self.resolved


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


class FakeProviders:
    def __init__(self, model: ToolCapableFakeModel) -> None:
        self.model = model

    def create_model(self, config: object) -> ToolCapableFakeModel:
        return self.model


class AcceptingValidator:
    def validate_execution(self, spec: AgentSpec) -> None:
        return None


async def test_inline_agent_executes_through_shared_runner() -> None:
    source = {"type": "inline", "spec": agent_spec().model_dump(mode="json")}
    workflow = validate(source)
    runner = RecordingAgentRunner()
    callback = RuntimeCallbackHandler("workflow")
    workflow_input = {"request": "Research this"}
    original = deepcopy(workflow_input)

    result = (
        await WorkflowGraphFactory(agent_runner=runner)
        .create(
            workflow,
            callback=callback,
            workflow_run_id="run-1",
        )
        .ainvoke(workflow_input)
    )

    assert result.output == "Answer"
    assert result.state == {"request": "Research this", "answer": "Answer"}
    assert result.executed_nodes == ("input", "agent", "output")
    assert workflow_input == original
    assert len(runner.calls) == 1
    assert runner.calls[0]["spec"] == agent_spec()
    assert runner.calls[0]["system_prompt"] is None
    assert runner.calls[0]["callbacks"] == [callback]
    assert runner.calls[0]["metadata"] == {
        "workflow_id": str(workflow.id),
        "workflow_run_id": "run-1",
        "node_id": "agent",
        "node_type": "agent",
    }


async def test_inline_agent_uses_langchain_agent_and_preserves_node_trace_context() -> None:
    source = {"type": "inline", "spec": agent_spec().model_dump(mode="json")}
    workflow = validate(source)
    callback = RuntimeCallbackHandler("workflow")
    runner = AgentRunner(
        FakeProviders(ToolCapableFakeModel(responses=[AIMessage(content="Answer")])),
        AcceptingValidator(),
        AgentFactory(ToolRegistry([])),
    )

    result = (
        await WorkflowGraphFactory(agent_runner=runner)
        .create(
            workflow,
            callback=callback,
            workflow_run_id="run-1",
        )
        .ainvoke({"request": "Research this"})
    )

    model_event = next(
        event for event in callback.trajectory if event.event_type == "model.started"
    )
    assert result.output == "Answer"
    assert model_event.payload["workflow_run_id"] == "run-1"
    assert model_event.payload["node_id"] == "agent"
    assert model_event.payload["node_type"] == "agent"


async def test_saved_agent_version_is_resolved_before_execution() -> None:
    workflow = validate({"type": "saved", "agent_version_id": str(VERSION_ID)})
    runner = RecordingAgentRunner()
    resolver = FakeAgentVersionResolver(
        ResolvedAgentVersion(spec=agent_spec(), system_prompt="Stored prompt")
    )

    result = (
        await WorkflowGraphFactory(
            agent_runner=runner,  # type: ignore[arg-type]
            agent_versions=resolver,
        )
        .create(workflow)
        .ainvoke({"request": "Research this"})
    )

    assert result.output == "Answer"
    assert resolver.calls == [VERSION_ID]
    assert runner.calls[0]["system_prompt"] == "Stored prompt"


async def test_unknown_saved_agent_version_is_a_structured_failure() -> None:
    workflow = validate({"type": "saved", "agent_version_id": str(VERSION_ID)})
    callback = RuntimeCallbackHandler("workflow")
    graph = WorkflowGraphFactory(
        agent_runner=RecordingAgentRunner(),  # type: ignore[arg-type]
        agent_versions=FakeAgentVersionResolver(None),
    ).create(workflow, callback=callback)

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "Research this"})

    assert caught.value.issue.code == "workflow.execution.agent_version_not_found"
    assert caught.value.issue.node_id == "agent"
    assert callback.trajectory[-1].event_type == "node.failed"


async def test_agent_failure_is_normalized_without_leaking_details() -> None:
    source = {"type": "inline", "spec": agent_spec().model_dump(mode="json")}
    workflow = validate(source)
    runner = RecordingAgentRunner(error=RuntimeError("private provider failure"))
    graph = WorkflowGraphFactory(agent_runner=runner).create(workflow)  # type: ignore[arg-type]

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "Research this"})

    assert caught.value.issue.code == "workflow.execution.agent_failed"
    assert "private provider failure" not in caught.value.issue.message


async def test_agent_requires_a_string_input() -> None:
    source = {"type": "inline", "spec": agent_spec().model_dump(mode="json")}
    data = workflow_data(source)
    data["nodes"][1]["config"]["input_path"] = "$.payload"
    workflow = WorkflowValidator(tool_names=[], provider_names=["test"]).validate(
        WorkflowSpec.model_validate(data)
    )
    graph = WorkflowGraphFactory(agent_runner=RecordingAgentRunner()).create(  # type: ignore[arg-type]
        workflow
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "valid", "payload": 42})

    assert caught.value.issue.code == "workflow.execution.agent_input_invalid"


async def test_agent_reports_a_missing_state_input() -> None:
    source = {"type": "inline", "spec": agent_spec().model_dump(mode="json")}
    data = workflow_data(source)
    data["nodes"][1]["config"]["input_path"] = "$.missing"
    workflow = WorkflowValidator(tool_names=[], provider_names=["test"]).validate(
        WorkflowSpec.model_validate(data)
    )
    graph = WorkflowGraphFactory(agent_runner=RecordingAgentRunner()).create(  # type: ignore[arg-type]
        workflow
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "valid"})

    assert caught.value.issue.code == "workflow.execution.agent_input_missing"
