from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

CapabilitySource = Literal["builtin", "mcp"]


@dataclass(frozen=True)
class RegisteredCapability:
    """One executable LangChain tool and its catalogue provenance."""

    id: str
    tool: BaseTool
    source: CapabilitySource
    origin: str | None = None

    def __post_init__(self) -> None:
        if self.id != self.tool.name:
            raise ValueError("capability id must match the LangChain tool name")
        if self.source == "mcp" and not self.origin:
            raise ValueError("MCP capabilities require a server origin")


class ToolRegistry:
    """A trusted registry shared by agents and deterministic connectors."""

    def __init__(self, tools: Iterable[BaseTool | RegisteredCapability] = ()) -> None:
        self._capabilities: dict[str, RegisteredCapability] = {}
        for item in tools:
            capability = (
                item
                if isinstance(item, RegisteredCapability)
                else RegisteredCapability(id=item.name, tool=item, source="builtin")
            )
            self.register(capability)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capabilities(self) -> tuple[RegisteredCapability, ...]:
        return tuple(self._capabilities.values())

    def register(self, capability: RegisteredCapability) -> None:
        """Register one trusted capability without replacing an existing ID."""
        if capability.id in self._capabilities:
            raise ValueError(f"capability already registered: {capability.id}")
        self._capabilities[capability.id] = capability

    def resolve(self, names: list[str]) -> list[BaseTool]:
        unknown = sorted(set(names) - self._capabilities.keys())
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(unknown)}")
        return [self._capabilities[name].tool for name in names]

    def resolve_one(self, name: str) -> BaseTool:
        """Resolve one allow-listed tool without exposing the registry mapping."""
        try:
            return self._capabilities[name].tool
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc
