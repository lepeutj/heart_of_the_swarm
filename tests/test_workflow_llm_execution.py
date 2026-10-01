from copy import deepcopy
from typing import Any

import pytest

from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def workflow_data() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        "name": "Tool-enabled LLM workflow",
        "description": "Execute an inline LangChain agent.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "research",
                "type": "llm",
                "name": "Research",
                "config": {
                    "agent": {
                        "name": "ResearchAgent",
                        "goal": "Research the request",
                        "instructions": "Use tools when they improve the answer.",
                        "model": {"provider": "test", "model_id": "test-model"},
                        "tools": ["web_search", "calculator"],
                    },
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
            {"source": "input", "target": "research"},
            {"source": "research", "target": "output"},
        ],
    }


def validated_workflow():
    return WorkflowValidator(
        tool_names=["web_search", "calculator"], provider_names=["test"]
    ).validate(WorkflowSpec.model_validate(workflow_data()))


class RecordingAgentRunner:
    def __init__(self, error: Exception | None = None, output: Any = "Answer") -> None:
        self.error = error
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, spec, agent_input, **kwargs) -> str:
        self.calls.append({"spec": spec, "input": agent_input, **kwargs})
        if self.error is not None:
            raise self.error
        return self.output


async def test_llm_node_delegates_tool_enabled_spec_to_agent_runner() -> None:
    workflow = validated_workflow()
    runner = RecordingAgentRunner()
    workflow_input = {"request": "Research this"}
    original = deepcopy(workflow_input)

    graph = WorkflowGraphFactory(agent_runner=runner).create(workflow)  # type: ignore[arg-type]
    result = await graph.ainvoke(workflow_input)

    assert result.output == "Answer"
    assert result.state == {"request": "Research this", "answer": "Answer"}
    assert result.executed_nodes == ("input", "research", "output")
    assert workflow_input == original
    assert len(runner.calls) == 1
    assert runner.calls[0]["spec"].tools == ["web_search", "calculator"]
    assert runner.calls[0]["input"] == "Research this"
    assert runner.calls[0]["system_prompt"] is None


async def test_llm_node_requires_agent_runtime() -> None:
    with pytest.raises(WorkflowExecutionError) as caught:
        await WorkflowGraphFactory().create(validated_workflow()).ainvoke({"request": "text"})

    assert caught.value.issue.code == "workflow.execution.agent_runtime_unavailable"
    assert caught.value.issue.node_id == "research"


async def test_llm_node_normalizes_missing_and_invalid_input() -> None:
    graph = WorkflowGraphFactory(agent_runner=RecordingAgentRunner()).create(validated_workflow())  # type: ignore[arg-type]

    with pytest.raises(WorkflowExecutionError) as missing:
        await graph.ainvoke({})
    with pytest.raises(WorkflowExecutionError) as invalid:
        await graph.ainvoke({"request": 42})

    assert missing.value.issue.code == "workflow.execution.agent_input_missing"
    assert invalid.value.issue.code == "workflow.execution.agent_input_invalid"


async def test_llm_node_normalizes_agent_failure() -> None:
    runner = RecordingAgentRunner(error=RuntimeError("provider unavailable"))
    graph = WorkflowGraphFactory(agent_runner=runner).create(validated_workflow())  # type: ignore[arg-type]

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke({"request": "text"})

    assert caught.value.issue.code == "workflow.execution.agent_failed"
    assert caught.value.__cause__.__class__ is RuntimeError


async def test_llm_node_passes_workflow_metadata_to_agent_runner() -> None:
    callback = RuntimeCallbackHandler("workflow")
    runner = RecordingAgentRunner()
    workflow = validated_workflow()

    graph = WorkflowGraphFactory(agent_runner=runner).create(  # type: ignore[arg-type]
        workflow, callback=callback, workflow_run_id="run-1"
    )
    await graph.ainvoke({"request": "text"})

    assert runner.calls[0]["callbacks"] == [callback]
    assert runner.calls[0]["metadata"] == {
        "workflow_id": str(workflow.id),
        "workflow_run_id": "run-1",
        "node_id": "research",
        "node_type": "llm",
    }


async def test_llm_node_maps_multiple_inputs_and_structured_outputs() -> None:
    data = workflow_data()
    response_schema = {
        "title": "ResearchResult",
        "description": "Structured research fields.",
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
        "additionalProperties": False,
    }
    data["output_schema"] = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
    }
    data["nodes"][1]["config"] = {
        "agent": data["nodes"][1]["config"]["agent"],
        "inputs": {
            "question": {"from_state": "$.request"},
            "documents": {"from_state": "$.documents"},
        },
        "outputs": {
            "answer": {"to_state": "$.research.answer"},
            "confidence": {"to_state": "$.research.confidence"},
        },
        "response_schema": response_schema,
    }
    data["nodes"][2]["config"] = {
        "outputs": {
            "answer": {"from_state": "$.research.answer"},
            "confidence": {"from_state": "$.research.confidence"},
        }
    }
    workflow = WorkflowValidator(
        tool_names=["web_search", "calculator"], provider_names=["test"]
    ).validate(WorkflowSpec.model_validate(data))
    runner = RecordingAgentRunner(output={"answer": "Result", "confidence": 0.86})

    result = (
        await WorkflowGraphFactory(agent_runner=runner)
        .create(workflow)
        .ainvoke(  # type: ignore[arg-type]
            {"request": "Research this", "documents": ["A", "B"]}
        )
    )

    assert runner.calls[0]["input"] == ('{"documents": ["A", "B"], "question": "Research this"}')
    assert runner.calls[0]["response_schema"] == response_schema
    assert result.state["research"] == {"answer": "Result", "confidence": 0.86}
    assert result.output == {"answer": "Result", "confidence": 0.86}


async def test_llm_node_rejects_missing_structured_output_field_atomically() -> None:
    data = workflow_data()
    data["nodes"][1]["config"] = {
        "agent": data["nodes"][1]["config"]["agent"],
        "inputs": {"request": {"from_state": "$.request"}},
        "outputs": {
            "answer": {"to_state": "$.research.answer"},
            "confidence": {"to_state": "$.research.confidence"},
        },
        "response_schema": {
            "title": "ResearchResult",
            "description": "Structured research fields.",
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["answer", "confidence"],
        },
    }
    workflow = WorkflowValidator(
        tool_names=["web_search", "calculator"], provider_names=["test"]
    ).validate(WorkflowSpec.model_validate(data))
    original = {"request": "Research this"}
    graph = WorkflowGraphFactory(  # type: ignore[arg-type]
        agent_runner=RecordingAgentRunner(output={"answer": "Result"})
    ).create(workflow)

    with pytest.raises(WorkflowExecutionError) as caught:
        await graph.ainvoke(original)

    assert caught.value.issue.code == "workflow.execution.invalid_agent_output"
    assert original == {"request": "Research this"}
