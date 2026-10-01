from collections import Counter
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Protocol, Self

from langchain_core.tools import BaseTool

from heart_of_the_swarm.observability import audit_event, audit_exception
from heart_of_the_swarm.tools.registry import RegisteredCapability, ToolRegistry


class MCPAdapterProtocol(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type, exc_value, traceback) -> None: ...

    async def list_tools(self, *, cache_mode: str = "use") -> list[BaseTool]: ...


def create_mcp_adapter(url: str) -> MCPAdapterProtocol:
    """Create LangChain's official MCP adapter lazily when MCP is configured."""
    from langchain.mcp import MCPAdapter

    return MCPAdapter(url)


class MCPToolLoader:
    """Discover remote MCP tools and register them in the shared tool registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        servers: dict[str, str],
        adapter_factory: Callable[[str], MCPAdapterProtocol] = create_mcp_adapter,
    ) -> None:
        self.registry = registry
        self.servers = dict(servers)
        self.adapter_factory = adapter_factory
        self._stack = AsyncExitStack()
        self._loaded = False

    async def load(self) -> tuple[str, ...]:
        """Connect to configured servers and add their LangChain tools atomically."""
        if self._loaded:
            return ()

        audit_event("mcp.discovery.started", server_count=len(self.servers))
        discovered: list[RegisteredCapability] = []
        try:
            for server_name, url in self.servers.items():
                adapter = await self._stack.enter_async_context(self.adapter_factory(url))
                tools = await adapter.list_tools(cache_mode="refresh")
                discovered.extend(
                    RegisteredCapability(
                        id=tool.name,
                        tool=tool,
                        source="mcp",
                        origin=server_name,
                    )
                    for tool in tools
                )

            existing = set(self.registry.names)
            counts = Counter(capability.id for capability in discovered)
            duplicates = sorted(
                name for name, count in counts.items() if count > 1 or name in existing
            )
            if duplicates:
                raise ValueError(f"duplicate MCP tool names: {', '.join(duplicates)}")

            for capability in discovered:
                self.registry.register(capability)
        except Exception:
            audit_exception("mcp.discovery.failed", server_count=len(self.servers))
            await self._stack.aclose()
            raise

        self._loaded = True
        audit_event(
            "mcp.discovery.completed",
            server_count=len(self.servers),
            capability_count=len(discovered),
        )
        return tuple(capability.id for capability in discovered)

    async def close(self) -> None:
        await self._stack.aclose()
