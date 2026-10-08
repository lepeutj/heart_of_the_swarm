from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import authorized_tool_view
from heart_of_the_swarm.validator import AgentSpecValidator


class AgentRunner:
    """Build and invoke one validated agent without persistence or transport concerns."""

    def __init__(
        self,
        providers: ProviderRegistry,
        validator: AgentSpecValidator,
        factory: AgentFactory,
        capability_authorizer: CapabilityAuthorizer | None = None,
    ) -> None:
        self.providers = providers
        self.validator = validator
        self.factory = factory
        self.capability_authorizer = capability_authorizer

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        *,
        system_prompt: str | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        metadata: Mapping[str, Any] | None = None,
        response_schema: dict[str, Any] | type[BaseModel] | None = None,
        security: ExecutionSecurityContext | None = None,
    ) -> Any:
        """Execute one agent and return text or LangChain-validated structured output."""
        self.validator.validate_execution(spec)
        if (self.capability_authorizer is None) != (security is None):
            raise ValueError("capability authorization requires an execution security context")
        tools = (
            None
            if self.capability_authorizer is None
            else await authorized_tool_view(
                self.factory.registry,
                spec.tools,
                self.capability_authorizer,
                security,
            )
        )
        model = self.providers.create_model(spec.model)
        factory_options: dict[str, Any] = {
            "system_prompt": system_prompt,
            "response_schema": response_schema,
        }
        if tools is not None:
            factory_options["tools"] = tools
        graph = self.factory.create(spec, model, **factory_options)
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": agent_input}]},
            config={"callbacks": list(callbacks), "metadata": dict(metadata or {})},
        )
        if response_schema is not None:
            structured = result.get("structured_response")
            if structured is None:
                raise RuntimeError("agent returned no structured response")
            if isinstance(structured, BaseModel):
                return structured.model_dump(mode="json")
            return structured
        final = next(
            (
                message
                for message in reversed(result.get("messages", []))
                if isinstance(message, AIMessage)
            ),
            None,
        )
        if final is None:
            raise RuntimeError("agent returned no final answer")
        return final.content if isinstance(final.content, str) else str(final.content)
