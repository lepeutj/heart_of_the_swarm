from typing import Any

from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pytest import MonkeyPatch

import heart_of_the_swarm.factory as factory_module
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import RegisteredCapability, ToolRegistry, create_default_registry
from heart_of_the_swarm.workflows import SupervisorDecision


@tool
def get_weather(city: str) -> str:
    """Return test weather for one city."""
    return city


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


def test_factory_builds_an_invokable_langgraph_agent() -> None:
    spec = AgentSpec(
        name="MathAgent",
        goal="Answer arithmetic questions",
        tools=["calculator"],
        instructions="Use the calculator and return its result.",
        model={"provider": "test", "model_id": "fake"},
    )
    model = ToolCapableFakeModel(responses=[AIMessage(content="Ready")])

    agent = AgentFactory(create_default_registry()).create(spec, model)
    result = agent.invoke({"messages": [{"role": "user", "content": "Say ready"}]})

    assert result["messages"][-1].content == "Ready"


def test_factory_maps_agent_spec_to_langchain_create_agent(monkeypatch: MonkeyPatch) -> None:
    spec = AgentSpec(
        name="MathAgent",
        goal="Answer arithmetic questions",
        tools=["calculator"],
        instructions="Use the calculator and return its result.",
        model={"provider": "test", "model_id": "fake"},
    )
    model = ToolCapableFakeModel(responses=[AIMessage(content="unused")])
    sentinel = object()
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(factory_module, "create_agent", fake_create_agent)

    result = AgentFactory(create_default_registry()).create(
        spec,
        model,
        system_prompt="Stored prompt",
    )

    assert result is sentinel
    assert captured["model"] is model
    assert [tool.name for tool in captured["tools"]] == ["calculator"]
    assert captured["system_prompt"] == "Stored prompt"
    assert captured["name"] == "MathAgent"


def test_factory_resolves_a_dynamically_registered_mcp_tool(monkeypatch: MonkeyPatch) -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredCapability(
            id="get_weather",
            tool=get_weather,
            source="mcp",
            origin="weather",
        )
    )
    spec = AgentSpec(
        name="WeatherAgent",
        goal="Report weather",
        tools=["get_weather"],
        instructions="Use the weather capability.",
        model={"provider": "test", "model_id": "fake"},
    )
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory_module, "create_agent", fake_create_agent)

    AgentFactory(registry).create(
        spec,
        ToolCapableFakeModel(responses=[AIMessage(content="unused")]),
    )

    assert captured["tools"] == [get_weather]


def test_factory_delegates_json_schema_to_langchain_structured_output(
    monkeypatch: MonkeyPatch,
) -> None:
    spec = AgentSpec(
        name="ResearchAgent",
        goal="Return structured research",
        tools=[],
        instructions="Return the requested fields.",
        model={"provider": "test", "model_id": "fake"},
    )
    schema = {
        "title": "Research",
        "description": "Research result",
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory_module, "create_agent", fake_create_agent)

    AgentFactory(create_default_registry()).create(
        spec,
        ToolCapableFakeModel(responses=[AIMessage(content="unused")]),
        response_schema=schema,
    )

    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema == schema


def test_factory_preserves_pydantic_union_for_structured_output(
    monkeypatch: MonkeyPatch,
) -> None:
    spec = AgentSpec(
        name="Supervisor",
        goal="Route work",
        tools=[],
        instructions="Return one routing decision.",
        model={"provider": "test", "model_id": "fake"},
    )
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory_module, "create_agent", fake_create_agent)

    AgentFactory(create_default_registry()).create(
        spec,
        ToolCapableFakeModel(responses=[AIMessage(content="unused")]),
        response_schema=SupervisorDecision,
    )

    strategy = captured["response_format"]
    assert isinstance(strategy, ToolStrategy)
    assert strategy.schema is SupervisorDecision
    assert len(strategy.schema_specs) == 1
