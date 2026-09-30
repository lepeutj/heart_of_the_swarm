from collections.abc import Sequence

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.validator import AgentSpecValidator


class AgentRunner:
    """Build and invoke one validated agent without persistence or transport concerns."""

    def __init__(
        self,
        providers: ProviderRegistry,
        validator: AgentSpecValidator,
        factory: AgentFactory,
    ) -> None:
        self.providers = providers
        self.validator = validator
        self.factory = factory

    async def invoke(
        self,
        spec: AgentSpec,
        agent_input: str,
        *,
        system_prompt: str | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
    ) -> str:
        """Execute one LangChain agent invocation and return its final assistant content."""
        self.validator.validate_execution(spec)
        model = self.providers.create_model(spec.model)
        graph = self.factory.create(spec, model, system_prompt=system_prompt)
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": agent_input}]},
            config={"callbacks": list(callbacks)},
        )
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
