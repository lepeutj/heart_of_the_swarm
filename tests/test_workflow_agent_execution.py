from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import pytest
from langchain_core.callbacks import BaseCallbackHandler

from heart_of_the_swarm.spec import AgentSpec
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


def validated_workflow():
    data = {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Saved agent workflow",
        "description": "Execute one saved agent version.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "agent",
                "type": "agent",
                "name": "Agent",
                "config": {
                    "agent_version_id": str(VERSION_ID),
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
    return WorkflowValidator(tool_names=[], provider_names=["test"]).validate(
        WorkflowSpec.model_validate(data)
    )


class RecordingAgentRunner:
    def __init__(self, error: Exception | None = None) -> None:
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
        return "Answer"


class FakeAgentVersionResolver:
    def __init__(self, resolved: ResolvedAgentVersion | None) -> None:
        self.resolved = resolved
        self.calls: list[UUID] = []

    async def resolve(self, agent_version_id: UUID) -> ResolvedAgentVersion | None:
        self.calls.append(agent_version_id)
        return self.resolved


async def test_saved_agent_version_is_resolved_before_execution() -> None:
    runner = RecordingAgentRunner()
    resolver = FakeAgentVersionResolver(
        ResolvedAgentVersion(spec=agent_spec(), system_prompt="Stored prompt")
    )
    graph = WorkflowGraphFactory(
        agent_runner=runner,  # type: ignore[arg-type]
        agent_versions=resolver,
    ).create(validated_workflow())

    result = await graph.ainvoke({"request": "Research this"})

    assert result.output == "Answer"
    assert resolver.calls == [VERSION_ID]
    assert runner.calls[0]["system_prompt"] == "Stored prompt"


async def test_unknown_saved_agent_version_is_a_structured_failure() -> None:
    graph = WorkflowGraphFactory(
        agent_runner=RecordingAgentRunner(),  # type: ignore[arg-type]
        agent_versions=FakeAgentVersionResolver(None),
    ).create(validated_workflow())

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "Research this"})

    assert caught.value.issue.code == "workflow.execution.agent_version_not_found"
    assert caught.value.issue.node_id == "agent"


async def test_saved_agent_requires_a_string_input() -> None:
    graph = WorkflowGraphFactory(
        agent_runner=RecordingAgentRunner(),  # type: ignore[arg-type]
        agent_versions=FakeAgentVersionResolver(
            ResolvedAgentVersion(spec=agent_spec(), system_prompt="Stored prompt")
        ),
    ).create(validated_workflow())

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": 42})

    assert caught.value.issue.code == "workflow.execution.agent_input_invalid"


async def test_saved_agent_failure_is_normalized() -> None:
    runner = RecordingAgentRunner(error=RuntimeError("private provider failure"))
    graph = WorkflowGraphFactory(
        agent_runner=runner,  # type: ignore[arg-type]
        agent_versions=FakeAgentVersionResolver(
            ResolvedAgentVersion(spec=agent_spec(), system_prompt="Stored prompt")
        ),
    ).create(validated_workflow())

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "Research this"})

    assert caught.value.issue.code == "workflow.execution.agent_failed"
    assert "private provider failure" not in caught.value.issue.message
