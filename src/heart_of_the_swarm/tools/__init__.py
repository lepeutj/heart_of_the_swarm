from heart_of_the_swarm.config import Settings, get_settings
from heart_of_the_swarm.tools.authorized import AuthorizedTool, authorized_tool_view
from heart_of_the_swarm.tools.builtin import (
    calculator,
    create_database_query,
    document_reader,
    http_get_json,
    rss_reader,
    web_search,
)
from heart_of_the_swarm.tools.mcp import MCPConnection, MCPToolLoader
from heart_of_the_swarm.tools.models import (
    CapabilityContract,
    CapabilityDescriptor,
    MCPServerCreate,
    MCPServerDetail,
    MCPServerStatus,
    MCPServerTestResult,
    MCPServerView,
    ToolCatalogueResponse,
)
from heart_of_the_swarm.tools.registry import RegisteredCapability, ToolRegistry


def create_default_registry(settings: Settings | None = None) -> ToolRegistry:
    configured = settings or get_settings()
    return ToolRegistry(
        [
            web_search,
            calculator,
            document_reader,
            http_get_json,
            rss_reader,
            create_database_query(configured),
        ]
    )


__all__ = [
    "CapabilityContract",
    "CapabilityDescriptor",
    "MCPServerCreate",
    "MCPServerDetail",
    "MCPServerStatus",
    "MCPServerTestResult",
    "MCPServerView",
    "MCPConnection",
    "MCPToolLoader",
    "RegisteredCapability",
    "ToolCatalogueResponse",
    "ToolRegistry",
    "AuthorizedTool",
    "authorized_tool_view",
    "create_default_registry",
]
