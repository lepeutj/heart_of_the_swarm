from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import create_default_registry


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


def test_factory_builds_an_invokable_langgraph_agent() -> None:
    spec = AgentSpec(
        name="MathAgent",
        goal="Answer arithmetic questions",
        tools=["calculator"],
        instructions="Use the calculator and return its result.",
        model={"provider": "test", "model_id": "fake"},
    )
    model = ToolCapableFakeModel(responses=[AIMessage(content="Ready")])

    agent = AgentFactory(create_default_registry()).create(spec, model)
    result = agent.invoke({"messages": [{"role": "user", "content": "Say ready"}]})

    assert result["messages"][-1].content == "Ready"
