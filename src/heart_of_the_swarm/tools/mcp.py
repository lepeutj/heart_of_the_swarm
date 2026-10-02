from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
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
        self._stacks: dict[str, AsyncExitStack] = {}
        self._statuses: dict[str, MCPServerStatus] = {}
        self._loaded = False

    @property
    def statuses(self) -> tuple["MCPServerStatus", ...]:
        return tuple(self._statuses.values())

    async def load(self) -> tuple[str, ...]:
        """Load every configured server without making one failure block the process."""
        if self._loaded:
            return ()

        audit_event("mcp.discovery.started", server_count=len(self.servers))
        discovered: list[str] = []
        for server_name in self.servers:
            discovered.extend(await self.refresh(server_name))
        self._loaded = True
        audit_event(
            "mcp.discovery.completed",
            server_count=len(self.servers),
            capability_count=len(discovered),
        )
        return tuple(discovered)

    async def refresh(self, server_name: str) -> tuple[str, ...]:
        """Refresh one server atomically and retain its previous catalogue on failure."""
        try:
            url = self.servers[server_name]
        except KeyError as exc:
            raise ValueError(f"unknown MCP server: {server_name}") from exc

        stack = AsyncExitStack()
        try:
            adapter = await stack.enter_async_context(self.adapter_factory(url))
            tools = await adapter.list_tools(cache_mode="refresh")
            capabilities = tuple(
                RegisteredCapability(
                    id=tool.name,
                    tool=tool,
                    source="mcp",
                    origin=server_name,
                )
                for tool in tools
            )
            self.registry.replace_source("mcp", server_name, capabilities)
        except Exception as exc:
            await stack.aclose()
            previous = self._statuses.get(server_name)
            self._statuses[server_name] = MCPServerStatus(
                name=server_name,
                state="error",
                tools=previous.tools if previous is not None else (),
                error=str(exc),
            )
            audit_exception("mcp.discovery.failed", server_name=server_name)
            return ()

        previous = self._stacks.get(server_name)
        self._stacks[server_name] = stack
        if previous is not None:
            await previous.aclose()
        names = tuple(capability.id for capability in capabilities)
        self._statuses[server_name] = MCPServerStatus(
            name=server_name,
            state="ready",
            tools=names,
        )
        audit_event(
            "mcp.server.ready",
            server_name=server_name,
            capability_count=len(names),
        )
        return names

    async def close(self) -> None:
        for stack in self._stacks.values():
            await stack.aclose()
        self._stacks.clear()


@dataclass(frozen=True)
class MCPServerStatus:
    """Current discovery state for one configured MCP server."""

    name: str
    state: str
    tools: tuple[str, ...]
    error: str | None = None
