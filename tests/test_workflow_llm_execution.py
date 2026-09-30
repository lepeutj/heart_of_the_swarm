from copy import deepcopy
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
        "name": "LLM workflow",
        "description": "Execute one controlled model call.",
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
                "id": "summarize",
                "type": "llm",
                "name": "Summarize",
                "config": {
                    "prompt": "Return a concise summary.",
                    "model": {"provider": "test", "model_id": "test-model"},
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
            {"source": "input", "target": "summarize"},
            {"source": "summarize", "target": "output"},
        ],
    }


def validated_workflow():
    spec = WorkflowSpec.model_validate(workflow_data())
    return WorkflowValidator(tool_names=[], provider_names=["test"]).validate(spec)


class RecordingModel:
    def __init__(self, response: AIMessage | None = None, error: Exception | None = None) -> None:
        self.response = response or AIMessage(content="Summary")
        self.error = error
        self.calls: list[tuple[list[Any], dict[str, Any]]] = []

    async def ainvoke(self, messages: list[Any], config: dict[str, Any]) -> AIMessage:
        self.calls.append((messages, config))
        if self.error is not None:
            raise self.error
        return self.response


class FakeProviders:
    def __init__(self, model: Any, errors: list[str] | None = None) -> None:
        self.model = model
        self.errors = errors or []
        self.configs: list[Any] = []

    def validate_model_execution(self, config: Any) -> list[str]:
        self.configs.append(config)
        return self.errors

    def create_model(self, config: Any) -> Any:
        self.configs.append(config)
        return self.model


async def test_llm_node_invokes_one_model_call_and_writes_output() -> None:
    workflow = validated_workflow()
    model = RecordingModel()
    providers = FakeProviders(model)
    workflow_input = {"request": "A long document"}
    original = deepcopy(workflow_input)

    result = await (
        WorkflowGraphFactory(providers=providers).create(workflow).ainvoke(workflow_input)
    )

    assert result.output == "Summary"
    assert result.state == {"request": "A long document", "answer": "Summary"}
    assert result.executed_nodes == ("input", "summarize", "output")
    assert workflow_input == original
    assert len(model.calls) == 1
    messages, invocation = model.calls[0]
    assert messages == [
        SystemMessage(content="Return a concise summary."),
        HumanMessage(content="A long document"),
    ]
    assert invocation["metadata"] == {
        "workflow_id": str(workflow.id),
        "workflow_run_id": None,
        "node_id": "summarize",
        "node_type": "llm",
    }


async def test_llm_node_requires_a_configured_runtime() -> None:
    with pytest.raises(WorkflowExecutionError) as caught:
        await WorkflowGraphFactory().create(validated_workflow()).ainvoke({"request": "text"})

    assert caught.value.issue.code == "workflow.execution.llm_runtime_unavailable"
    assert caught.value.issue.node_id == "summarize"


async def test_llm_node_normalizes_model_policy_failure() -> None:
    providers = FakeProviders(RecordingModel(), ["selected model is not allowed"])

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(providers=providers)
            .create(validated_workflow())
            .ainvoke({"request": "text"})
        )

    assert caught.value.issue.code == "workflow.execution.llm_model_invalid"
    assert str(caught.value) == "selected model is not allowed"


async def test_llm_node_normalizes_missing_and_invalid_input() -> None:
    data = workflow_data()
    data["input_schema"] = {"type": "object"}
    workflow = WorkflowValidator(tool_names=[], provider_names=["test"]).validate(
        WorkflowSpec.model_validate(data)
    )
    graph = WorkflowGraphFactory(providers=FakeProviders(RecordingModel())).create(workflow)

    with pytest.raises(WorkflowExecutionError) as missing:
        await graph.ainvoke({})
    with pytest.raises(WorkflowExecutionError) as invalid:
        await graph.ainvoke({"request": 42})

    assert missing.value.issue.code == "workflow.execution.llm_input_missing"
    assert invalid.value.issue.code == "workflow.execution.llm_input_invalid"


async def test_llm_node_normalizes_model_failure() -> None:
    providers = FakeProviders(RecordingModel(error=RuntimeError("provider unavailable")))

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(providers=providers)
            .create(validated_workflow())
            .ainvoke({"request": "text"})
        )

    assert caught.value.issue.code == "workflow.execution.llm_failed"
    assert caught.value.__cause__.__class__ is RuntimeError


async def test_llm_node_model_events_inherit_workflow_context() -> None:
    callback = RuntimeCallbackHandler("workflow")
    model = FakeMessagesListChatModel(responses=[AIMessage(content="Summary")])
    result = await (
        WorkflowGraphFactory(providers=FakeProviders(model))
        .create(validated_workflow(), callback=callback, workflow_run_id="run-1")
        .ainvoke({"request": "text"})
    )

    assert result.output == "Summary"
    model_events = [event for event in callback.trajectory if event.event_type.startswith("model.")]
    assert [event.event_type for event in model_events] == ["model.started", "model.completed"]
    assert all(event.payload["workflow_run_id"] == "run-1" for event in model_events)
    assert all(event.payload["node_id"] == "summarize" for event in model_events)
