"""Discover and invoke the sales MCP server through LangChain's production adapter."""

import argparse
import asyncio
import json

from langchain.mcp import MCPAdapter

from heart_of_the_swarm.tools.registry import RegisteredCapability, ToolRegistry


async def run(url: str, *, catalogue_only: bool) -> None:
    """Print the discovered catalogue and optionally verify live read-only database access."""
    async with MCPAdapter(url) as adapter:
        tools = await adapter.list_tools(cache_mode="refresh")
        registry = ToolRegistry(
            RegisteredCapability(id=tool.name, tool=tool, source="mcp", origin="sales")
            for tool in tools
        )
        print(json.dumps({"tools": sorted(registry.names)}, indent=2))
        if catalogue_only:
            return

        # Exercise Heart's production MCP invocation and structured-result normalization boundary.
        access = await registry.invoke_one("database_access", {})
        summary = await registry.invoke_one("sales_summary", {"days": 30})
        if not access["transaction_read_only"] or access["may_insert_orders"]:
            raise RuntimeError("sales MCP database identity is not read-only")
        print(json.dumps({"database_access": access, "sales_summary": summary}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--catalogue-only", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.url, catalogue_only=arguments.catalogue_only))


if __name__ == "__main__":
    main()
