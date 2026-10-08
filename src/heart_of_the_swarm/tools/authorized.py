from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.tools.registry import ToolRegistry


class AuthorizedTool(BaseTool):
    """Preserve a LangChain tool contract while rechecking permission per invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    wrapped: BaseTool
    authorizer: CapabilityAuthorizer
    security: ExecutionSecurityContext

    async def ainvoke(
        self,
        input: str | dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        await self.authorizer.require_invocation(
            self.security.principal,
            self.name,
            context=self.security.authorization,
        )
        return await self.wrapped.ainvoke(input, config=config, **kwargs)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("authorized capabilities require asynchronous execution")


async def authorized_tool_view(
    registry: ToolRegistry,
    requested: list[str],
    authorizer: CapabilityAuthorizer,
    security: ExecutionSecurityContext,
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
        )
        for tool in registry.resolve(list(allowed))
    ]
