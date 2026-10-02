from collections.abc import Mapping, Sequence
from typing import Any, Self

import pytest
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool, ToolException, tool

from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import MCPToolLoader, RegisteredCapability, ToolRegistry
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
)


def weather_server() -> FastMCP:
    server = FastMCP("weather-test")

    @server.tool
    async def get_weather(city: str) -> dict[str, object]:
        """Return structured test weather for one city."""
        return {"summary": f"Sunny in {city}", "temperature": 21}

    @server.tool
    async def greet(name: str) -> str:
        """Return one textual greeting."""
        return f"Hello {name}"

    @server.tool
    async def fail_weather(city: str) -> str:
        """Raise a remote tool error."""
        raise ValueError(f"No weather for {city}")

    return server


@tool
def local_weather(city: str) -> str:
    """Return local test weather."""
    return f"Local weather in {city}"


@tool
def failing_local_tool(value: str) -> str:
    """Fail while asking LangChain to convert the exception into a tool result."""
    raise ToolException(f"Rejected {value}")


failing_local_tool.handle_tool_error = True


class FailingAdapter:
    async def __aenter__(self) -> Self:
        raise ConnectionError("server unavailable")

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None

    async def list_tools(self, *, cache_mode: str = "use") -> list[BaseTool]:
        return []


class RecordingAgentRunner:
    def __init__(self) -> None:
        self.inputs: list[str] = []

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
        self.inputs.append(agent_input)
        return f"Report: {agent_input}"


def connector_workflow(capability_id: str, *, with_agent: bool = False) -> WorkflowSpec:
    nodes: list[dict[str, Any]] = [
        {"id": "input", "type": "input", "name": "Input", "config": {}},
        {
            "id": "weather",
            "type": "connector",
            "name": "Weather",
            "config": {
                "capability_id": capability_id,
                "inputs": {"city": {"from_state": "$.city"}},
                "outputs": {
                    "summary": {"to_state": "$.weather.summary"},
                    "temperature": {"to_state": "$.weather.temperature"},
                },
            },
        },
    ]
    edges = [{"source": "input", "target": "weather"}]
    if with_agent:
        nodes.append(
            {
                "id": "agent",
                "type": "agent",
                "name": "Reporter",
                "config": {
                    "source": {
                        "type": "inline",
                        "agent": {
                            "name": "Reporter",
                            "goal": "Summarize weather",
                            "instructions": "Return a concise report.",
                            "model": {"provider": "test", "model_id": "test-model"},
                            "tools": [],
                        },
                    },
                    "inputs": {
                        "summary": {"from_state": "$.weather.summary"},
                        "temperature": {"from_state": "$.weather.temperature"},
                    },
                    "outputs": {"result": {"to_state": "$.report"}},
                },
            }
        )
        edges.append({"source": "weather", "target": "agent"})
        output_path = "$.report"
        output_source = "agent"
    else:
        output_path = "$.weather"
        output_source = "weather"
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "name": "Output",
            "config": {"output_path": output_path},
        }
    )
    edges.append({"source": output_source, "target": "output"})
    return WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "MCP weather workflow",
            "description": "Exercise the real MCP connector boundary.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": ["object", "string"]},
            "entrypoint": "input",
            "nodes": nodes,
            "edges": edges,
        }
    )


async def test_real_mcp_discovery_exposes_schema_and_structured_result() -> None:
    registry = ToolRegistry()
    loader = MCPToolLoader(
        registry,
        {"weather": "in-process"},
        adapter_factory=lambda _: MCPAdapter(weather_server()),
    )

    loaded = await loader.load()
    capability = next(item for item in registry.capabilities if item.id == "get_weather")
    result = await registry.invoke_one("get_weather", {"city": "Paris"})

    assert loaded == ("get_weather", "greet", "fail_weather")
    assert capability.source == "mcp"
    assert capability.origin == "weather"
    assert capability.input_schema["required"] == ["city"]
    assert registry.resolve(["get_weather"]) == [capability.tool]
    assert result == {"summary": "Sunny in Paris", "temperature": 21}
    await loader.close()


async def test_real_mcp_text_result_remains_mappable() -> None:
    registry = ToolRegistry()
    loader = MCPToolLoader(
        registry,
        {"weather": "in-process"},
        adapter_factory=lambda _: MCPAdapter(weather_server()),
    )
    await loader.load()

    assert await registry.invoke_one("greet", {"name": "Ada"}) == {"result": "Hello Ada"}
    await loader.close()


async def test_real_mcp_tool_executes_through_connector_agent_and_output() -> None:
    registry = ToolRegistry()
    loader = MCPToolLoader(
        registry,
        {"weather": "in-process"},
        adapter_factory=lambda _: MCPAdapter(weather_server()),
    )
    await loader.load()
    workflow = WorkflowValidator(lambda: registry.names, ["test"]).validate(
        connector_workflow("get_weather", with_agent=True)
    )
    runner = RecordingAgentRunner()

    graph = WorkflowGraphFactory(
        capabilities=registry,
        agent_runner=runner,  # type: ignore[arg-type]
    ).create(workflow)
    result = await graph.ainvoke({"city": "Paris"})

    assert runner.inputs == ['{"summary": "Sunny in Paris", "temperature": 21}']
    assert result.output == 'Report: {"summary": "Sunny in Paris", "temperature": 21}'
    assert result.executed_nodes == ("input", "weather", "agent", "output")
    await loader.close()


async def test_real_mcp_error_becomes_connector_failure() -> None:
    registry = ToolRegistry()
    loader = MCPToolLoader(
        registry,
        {"weather": "in-process"},
        adapter_factory=lambda _: MCPAdapter(weather_server()),
    )
    await loader.load()
    workflow = connector_workflow("fail_weather")
    workflow.nodes[1].config["outputs"] = {"result": {"to_state": "$.weather"}}
    validated = WorkflowValidator(lambda: registry.names, []).validate(workflow)

    with pytest.raises(WorkflowExecutionError) as caught:
        await (
            WorkflowGraphFactory(capabilities=registry).create(validated).ainvoke({"city": "Paris"})
        )

    assert caught.value.issue.code == "workflow.execution.connector_failed"
    assert caught.value.issue.node_id == "weather"
    await loader.close()


async def test_registry_does_not_treat_handled_tool_error_as_success() -> None:
    registry = ToolRegistry([failing_local_tool])

    with pytest.raises(ToolException, match="Rejected input"):
        await registry.invoke_one("failing_local_tool", {"value": "input"})


async def test_unavailable_mcp_server_does_not_hide_healthy_tools() -> None:
    registry = ToolRegistry([local_weather])
    server = weather_server()
    loader = MCPToolLoader(
        registry,
        {"offline": "offline", "weather": "healthy"},
        adapter_factory=lambda url: FailingAdapter() if url == "offline" else MCPAdapter(server),
    )

    loaded = await loader.load()

    assert loaded == ("get_weather", "greet", "fail_weather")
    assert "local_weather" in registry.names
    assert "get_weather" in registry.names
    assert [(status.name, status.state) for status in loader.statuses] == [
        ("offline", "error"),
        ("weather", "ready"),
    ]
    await loader.close()


async def test_failed_refresh_keeps_previous_server_catalogue() -> None:
    registry = ToolRegistry()
    fail_refresh = False

    def adapter_factory(_: str):
        return FailingAdapter() if fail_refresh else MCPAdapter(weather_server())

    loader = MCPToolLoader(registry, {"weather": "source"}, adapter_factory=adapter_factory)
    await loader.load()
    original = registry.resolve_one("get_weather")
    fail_refresh = True

    assert await loader.refresh("weather") == ()
    assert registry.resolve_one("get_weather") is original
    assert loader.statuses[0].state == "error"
    await loader.close()


def test_replace_source_is_atomic_when_refreshed_catalogue_collides() -> None:
    registry = ToolRegistry([local_weather])
    remote_tool = local_weather.model_copy(update={"name": "remote_weather"})
    original = RegisteredCapability(
        id="remote_weather",
        tool=remote_tool,
        source="mcp",
        origin="weather",
    )
    registry.register(original)
    collision = RegisteredCapability(
        id="local_weather",
        tool=local_weather,
        source="mcp",
        origin="weather",
    )

    with pytest.raises(ValueError, match="already registered"):
        registry.replace_source("mcp", "weather", [collision])

    assert registry.resolve_one("remote_weather") is original.tool
