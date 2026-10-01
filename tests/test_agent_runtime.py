from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.spec import AgentSpec


def make_spec() -> AgentSpec:
    return AgentSpec(
        name="ResearchAgent",
        goal="Research one topic",
        instructions="Return a concise answer.",
        tools=[],
        model={"provider": "test", "model_id": "test-model"},
    )


class RecordingValidator:
    def __init__(self) -> None:
        self.spec: AgentSpec | None = None

    def validate_execution(self, spec: AgentSpec) -> AgentSpec:
        self.spec = spec
        return spec


class RecordingProviders:
    def __init__(self, model: object) -> None:
        self.model = model
        self.config = None

    def create_model(self, config: object) -> object:
        self.config = config
        return self.model


class FakeGraph:
    def __init__(self, messages: list[object], structured_response: object | None = None) -> None:
        self.messages = messages
        self.structured_response = structured_response
        self.input: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(self, value: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.input = value
        self.config = config
        result = {"messages": self.messages}
        if self.structured_response is not None:
            result["structured_response"] = self.structured_response
        return result


class RecordingFactory:
    def __init__(self, graph: FakeGraph) -> None:
        self.graph = graph
        self.call: tuple[AgentSpec, object, str | None, dict[str, Any] | None] | None = None

    def create(
        self,
        spec: AgentSpec,
        model: object,
        system_prompt: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> FakeGraph:
        self.call = (spec, model, system_prompt, response_schema)
        return self.graph


async def test_runner_validates_builds_and_invokes_agent() -> None:
    spec = make_spec()
    model = object()
    callback = BaseCallbackHandler()
    validator = RecordingValidator()
    providers = RecordingProviders(model)
    graph = FakeGraph([HumanMessage(content="Question"), AIMessage(content="Answer")])
    factory = RecordingFactory(graph)
    runner = AgentRunner(providers, validator, factory)  # type: ignore[arg-type]

    output = await runner.invoke(
        spec,
        "Question",
        system_prompt="Stored prompt",
        callbacks=[callback],
        metadata={"workflow_id": "workflow-1"},
    )

    assert output == "Answer"
    assert validator.spec is spec
    assert providers.config is spec.model
    assert factory.call == (spec, model, "Stored prompt", None)
    assert graph.input == {"messages": [{"role": "user", "content": "Question"}]}
    assert graph.config == {
        "callbacks": [callback],
        "metadata": {"workflow_id": "workflow-1"},
    }


async def test_runner_rejects_result_without_final_assistant_message() -> None:
    spec = make_spec()
    graph = FakeGraph([HumanMessage(content="Question")])
    runner = AgentRunner(  # type: ignore[arg-type]
        RecordingProviders(object()),
        RecordingValidator(),
        RecordingFactory(graph),
    )

    with pytest.raises(RuntimeError, match="agent returned no final answer"):
        await runner.invoke(spec, "Question")


async def test_runner_returns_langchain_structured_response() -> None:
    schema = {
        "title": "Research",
        "description": "Research result",
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    graph = FakeGraph([], structured_response={"answer": "Structured answer"})
    factory = RecordingFactory(graph)
    runner = AgentRunner(  # type: ignore[arg-type]
        RecordingProviders(object()),
        RecordingValidator(),
        factory,
    )

    output = await runner.invoke(make_spec(), "Question", response_schema=schema)

    assert output == {"answer": "Structured answer"}
    assert factory.call is not None
    assert factory.call[3] == schema
