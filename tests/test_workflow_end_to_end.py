from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.workflows import WorkflowGraphFactory, WorkflowSpec, WorkflowValidator


class ToolCapableFakeModel(FakeMessagesListChatModel):
    """Return deterministic messages while accepting LangChain tool binding."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


class StaticProviderRegistry:
    def __init__(self, model: ToolCapableFakeModel) -> None:
        self.model = model

    def create_model(self, config: object) -> ToolCapableFakeModel:
        return self.model


class AcceptingAgentValidator:
    def validate_execution(self, spec: AgentSpec) -> AgentSpec:
        return spec


async def test_structured_agent_workflow_runs_end_to_end() -> None:
    response_schema = {
        "title": "ResearchResult",
        "description": "A concise research result.",
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
        "additionalProperties": False,
    }
    workflow = WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "Simple research",
            "description": "Return an answer and confidence score.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["question", "context"],
            },
            "output_schema": response_schema,
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "research",
                    "type": "llm",
                    "name": "Research",
                    "config": {
                        "agent": {
                            "name": "ResearchAgent",
                            "goal": "Answer one research question",
                            "instructions": "Use the supplied context and report confidence.",
                            "model": {"provider": "test", "model_id": "fake"},
                            "tools": [],
                        },
                        "inputs": {
                            "question": {"from_state": "$.question"},
                            "context": {"from_state": "$.context"},
                        },
                        "outputs": {
                            "answer": {"to_state": "$.research.answer"},
                            "confidence": {"to_state": "$.research.confidence"},
                        },
                        "response_schema": response_schema,
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {
                        "outputs": {
                            "answer": {"from_state": "$.research.answer"},
                            "confidence": {"from_state": "$.research.confidence"},
                        }
                    },
                },
            ],
            "edges": [
                {"source": "input", "target": "research"},
                {"source": "research", "target": "output"},
            ],
        }
    )
    validated = WorkflowValidator(tool_names=[], provider_names=["test"]).validate(workflow)
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ResearchResult",
                        "args": {"answer": "LangGraph shares state.", "confidence": 0.91},
                        "id": "structured-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviderRegistry(model),
        AcceptingAgentValidator(),
        AgentFactory(create_default_registry()),
    )

    result = (
        await WorkflowGraphFactory(agent_runner=runner)
        .create(validated)
        .ainvoke(  # type: ignore[arg-type]
            {"question": "How does the graph pass data?", "context": "Use shared state."}
        )
    )

    assert result.executed_nodes == ("input", "research", "output")
    assert result.state["research"] == {
        "answer": "LangGraph shares state.",
        "confidence": 0.91,
    }
    assert result.output == {
        "answer": "LangGraph shares state.",
        "confidence": 0.91,
    }
