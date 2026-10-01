from heart_of_the_swarm.config import Settings, get_settings
from heart_of_the_swarm.tools.builtin import (
    calculator,
    create_database_query,
    document_reader,
    http_get_json,
    web_search,
)
from heart_of_the_swarm.tools.mcp import MCPToolLoader
from heart_of_the_swarm.tools.registry import RegisteredCapability, ToolRegistry


def create_default_registry(settings: Settings | None = None) -> ToolRegistry:
    configured = settings or get_settings()
    return ToolRegistry(
        [web_search, calculator, document_reader, http_get_json, create_database_query(configured)]
    )


__all__ = ["MCPToolLoader", "RegisteredCapability", "ToolRegistry", "create_default_registry"]
