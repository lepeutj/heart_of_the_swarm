from types import TracebackType
from typing import Self

import pytest
from langchain_core.tools import BaseTool, tool

from heart_of_the_swarm.tools import MCPToolLoader, RegisteredCapability, ToolRegistry
from heart_of_the_swarm.workflows import WorkflowGraphFactory, WorkflowSpec, WorkflowValidator


@tool
def get_weather(city: str) -> str:
    """Return test weather for one city."""
    return f"Sunny in {city}"


class FakeMCPAdapter:
    def __init__(self, tools: list[BaseTool]) -> None:
        self.tools = tools
        self.entered = False
        self.closed = False
        self.cache_modes: list[str] = []

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    async def list_tools(self, *, cache_mode: str = "use") -> list[BaseTool]:
        self.cache_modes.append(cache_mode)
        return self.tools


def test_registry_accepts_dynamic_capabilities_and_preserves_provenance() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredCapability(
            id="get_weather",
            tool=get_weather,
            source="mcp",
            origin="weather",
        )
    )

    assert registry.names == ("get_weather",)
    assert registry.resolve_one("get_weather") is get_weather
    assert registry.capabilities[0].source == "mcp"
    assert registry.capabilities[0].origin == "weather"

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            RegisteredCapability(
                id="get_weather",
                tool=get_weather,
                source="mcp",
                origin="other",
            )
        )


async def test_mcp_loader_discovers_and_registers_langchain_tools() -> None:
    registry = ToolRegistry()
    adapter = FakeMCPAdapter([get_weather])
    loader = MCPToolLoader(
        registry,
        {"weather": "https://weather.example.com/mcp"},
        adapter_factory=lambda url: adapter,
    )

    loaded = await loader.load()

    assert loaded == ("get_weather",)
    assert registry.resolve(["get_weather"]) == [get_weather]
    assert adapter.entered is True
    assert adapter.cache_modes == ["refresh"]

    await loader.close()
    assert adapter.closed is True


async def test_mcp_loader_rejects_collisions_without_partial_registration() -> None:
    registry = ToolRegistry([get_weather])
    adapter = FakeMCPAdapter([get_weather])
    loader = MCPToolLoader(
        registry,
        {"weather": "https://weather.example.com/mcp"},
        adapter_factory=lambda url: adapter,
    )

    with pytest.raises(ValueError, match="duplicate MCP tool names"):
        await loader.load()

    assert registry.names == ("get_weather",)
    assert adapter.closed is True


async def test_discovered_mcp_tool_executes_as_a_connector() -> None:
    registry = ToolRegistry()
    validator = WorkflowValidator(lambda: registry.names, [])
    adapter = FakeMCPAdapter([get_weather])
    loader = MCPToolLoader(
        registry,
        {"weather": "https://weather.example.com/mcp"},
        adapter_factory=lambda url: adapter,
    )
    await loader.load()
    workflow = WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "Weather connector",
            "description": "Invoke one discovered MCP tool.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "string"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "weather",
                    "type": "connector",
                    "name": "Weather",
                    "config": {
                        "capability_id": "get_weather",
                        "inputs": {"city": {"from_state": "$.city"}},
                        "outputs": {"result": {"to_state": "$.weather"}},
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.weather"},
                },
            ],
            "edges": [
                {"source": "input", "target": "weather"},
                {"source": "weather", "target": "output"},
            ],
        }
    )

    result = (
        await WorkflowGraphFactory(capabilities=registry)
        .create(validator.validate(workflow))
        .ainvoke({"city": "Paris"})
    )

    assert result.output == "Sunny in Paris"
    await loader.close()
