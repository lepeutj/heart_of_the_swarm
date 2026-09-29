from collections.abc import Iterable
from types import MappingProxyType

from langchain_core.tools import BaseTool


class ToolRegistry:
    """An immutable allow-list of executable capabilities."""

    def __init__(self, tools: Iterable[BaseTool]) -> None:
        tool_list = list(tools)
        by_name = {tool.name: tool for tool in tool_list}
        if len(by_name) != len(tool_list):
            raise ValueError("tool names must be unique")
        self._tools = MappingProxyType(by_name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def resolve(self, names: list[str]) -> list[BaseTool]:
        unknown = sorted(set(names) - self._tools.keys())
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(unknown)}")
        return [self._tools[name] for name in names]

    def resolve_one(self, name: str) -> BaseTool:
        """Resolve one allow-listed tool without exposing the registry mapping."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc
