from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.tools.registry import ToolRegistry, validate_public_tool_arguments

if TYPE_CHECKING:
    from heart_of_the_swarm.workflows.execution.execution_budget import ExecutionBudget


def _validated_tool_input(
    tool: BaseTool,
    input: str | dict[str, Any],
) -> str | dict[str, Any]:
    """Validate model-selected arguments before authorization or budget reservation."""
    if not isinstance(input, dict):
        return input
    if input.get("type") == "tool_call":
        arguments = validate_public_tool_arguments(tool, input.get("args", {}))
        return {**input, "args": arguments}
    return validate_public_tool_arguments(tool, input)


class BudgetedTool(BaseTool):
    """Preserve one tool contract while reserving budget before its real side effect."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    wrapped: BaseTool
    execution_budget: Any

    async def ainvoke(
        self,
        input: str | dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        validated = _validated_tool_input(self.wrapped, input)
        await self.execution_budget.consume_tool_call()
        return await self.wrapped.ainvoke(validated, config=config, **kwargs)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("budgeted capabilities require asynchronous execution")


class AuthorizedTool(BaseTool):
    """Preserve a LangChain tool contract while rechecking permission per invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    wrapped: BaseTool
    authorizer: CapabilityAuthorizer
    security: ExecutionSecurityContext
    execution_budget: Any = None

    async def ainvoke(
        self,
        input: str | dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        validated = _validated_tool_input(self.wrapped, input)
        await self.authorizer.require_invocation(
            self.security.principal,
            self.name,
            context=self.security.authorization,
        )
        if self.execution_budget is not None:
            await self.execution_budget.consume_tool_call()
        return await self.wrapped.ainvoke(validated, config=config, **kwargs)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("authorized capabilities require asynchronous execution")


async def authorized_tool_view(
    registry: ToolRegistry,
    requested: list[str],
    authorizer: CapabilityAuthorizer,
    security: ExecutionSecurityContext,
    execution_budget: ExecutionBudget | None = None,
) -> list[BaseTool]:
    """Build one isolated model-facing tool list without mutating the shared registry."""
    allowed = await authorizer.allowed_capabilities(
        security.principal,
        requested,
        context=security.authorization,
    )
    return [
        AuthorizedTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            return_direct=tool.return_direct,
            tags=tool.tags,
            metadata=tool.metadata,
            response_format=tool.response_format,
            wrapped=tool,
            authorizer=authorizer,
            security=security,
            execution_budget=execution_budget,
        )
        for tool in registry.resolve(list(allowed))
    ]


def budgeted_tool_view(
    registry: ToolRegistry,
    requested: list[str],
    execution_budget: ExecutionBudget,
) -> list[BaseTool]:
    """Build a budget-enforcing tool list when capability authorization is disabled."""
    return [
        BudgetedTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            return_direct=tool.return_direct,
            tags=tool.tags,
            metadata=tool.metadata,
            response_format=tool.response_format,
            wrapped=tool,
            execution_budget=execution_budget,
        )
        for tool in registry.resolve(requested)
    ]


async def approval_capable_tool_view(
    registry: ToolRegistry,
    requested: list[str],
    authorizer: CapabilityAuthorizer,
    security: ExecutionSecurityContext,
) -> list[BaseTool]:
    """Expose tools that a durable agent may invoke directly or after approval."""
    exposed = await authorizer.approval_capable_capabilities(
        security.principal,
        requested,
        context=security.authorization,
    )
    return registry.resolve(list(exposed))
