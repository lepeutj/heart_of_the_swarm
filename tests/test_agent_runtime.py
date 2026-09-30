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
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.input: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(self, value: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.input = value
        self.config = config
        return {"messages": self.messages}


class RecordingFactory:
    def __init__(self, graph: FakeGraph) -> None:
        self.graph = graph
        self.call: tuple[AgentSpec, object, str | None] | None = None

    def create(
        self,
        spec: AgentSpec,
        model: object,
        system_prompt: str | None = None,
    ) -> FakeGraph:
        self.call = (spec, model, system_prompt)
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
    )

    assert output == "Answer"
    assert validator.spec is spec
    assert providers.config is spec.model
    assert factory.call == (spec, model, "Stored prompt")
    assert graph.input == {"messages": [{"role": "user", "content": "Question"}]}
    assert graph.config == {"callbacks": [callback]}


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
