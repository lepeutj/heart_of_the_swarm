from typing import Any

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pytest import MonkeyPatch

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import builtin as builtin_tools
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
                    "type": "agent",
                    "name": "Research",
                    "config": {
                        "source": {
                            "type": "inline",
                            "agent": {
                                "name": "ResearchAgent",
                                "goal": "Answer one research question",
                                "instructions": "Use the supplied context and report confidence.",
                                "model": {"provider": "test", "model_id": "fake"},
                                "tools": [],
                            },
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


async def test_agent_can_generate_and_execute_another_rss_agent(
    monkeypatch: MonkeyPatch,
) -> None:
    generated_workflow = {
        "schema_version": "1",
        "id": "561bb5e7-3ca0-44ae-99ec-37f98c9c24ba",
        "name": "Generated RSS research agent",
        "description": "A workflow designed by another agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feed_url": {"type": "string"},
                "request": {"type": "string"},
            },
            "required": ["feed_url", "request"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        },
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "rss_agent",
                "type": "agent",
                "name": "RSS research agent",
                "config": {
                    "source": {
                        "type": "inline",
                        "agent": {
                            "name": "GeneratedRSSAgent",
                            "goal": "Read a feed and summarize its latest topics in French.",
                            "instructions": (
                                "Call rss_reader exactly once with the supplied feed URL and "
                                "return a concise French summary."
                            ),
                            "model": {"provider": "test", "model_id": "worker"},
                            "tools": ["rss_reader"],
                        },
                    },
                    "inputs": {
                        "feed_url": {"from_state": "$.feed_url"},
                        "request": {"from_state": "$.request"},
                    },
                    "outputs": {"answer": {"to_state": "$.answer"}},
                },
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"outputs": {"result": {"from_state": "$.answer"}}},
            },
        ],
        "edges": [
            {"source": "input", "target": "rss_agent"},
            {"source": "rss_agent", "target": "output"},
        ],
    }
    workflow_schema = WorkflowSpec.model_json_schema()
    designer_model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": workflow_schema["title"],
                        "args": generated_workflow,
                        "id": "generated-workflow-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    registry = create_default_registry()
    designer = AgentRunner(  # type: ignore[arg-type]
        StaticProviderRegistry(designer_model),
        AcceptingAgentValidator(),
        AgentFactory(registry),
    )
    designer_spec = AgentSpec.model_validate(
        {
            "name": "WorkflowDesigner",
            "goal": "Create an executable workflow containing another agent.",
            "instructions": "Return the smallest valid WorkflowSpec for the request.",
            "model": {"provider": "test", "model_id": "designer"},
            "tools": [],
        }
    )

    designed_data = await designer.invoke(
        designer_spec,
        (
            "Create a sequential workflow containing an inline agent. The agent must call "
            "rss_reader once, identify the main themes, and answer the request in French."
        ),
        response_schema=workflow_schema,
    )
    designed_spec = WorkflowSpec.model_validate(designed_data)
    validated = WorkflowValidator(
        tool_names=list(registry.names),
        provider_names=["test"],
    ).validate(designed_spec)

    async def rss_response(url: str) -> httpx.Response:
        feed = """<rss><channel>
        <item><title>New open model released</title><link>https://example.com/model</link>
        <description>A smaller open model improves tool use.</description></item>
        <item><title>Agents enter production</title><link>https://example.com/agents</link>
        <description>Teams report new agent observability practices.</description></item>
        </channel></rss>"""
        return httpx.Response(
            200,
            content=feed.encode(),
            headers={"Content-Type": "application/rss+xml"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", rss_response)
    worker_model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "rss_reader",
                        "args": {"url": "https://example.com/feed.xml", "max_items": 5},
                        "id": "rss-reader-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "Les thèmes principaux sont les modèles ouverts, l'utilisation d'outils "
                    "et l'observabilité des agents en production."
                )
            ),
        ]
    )
    generated_agent_runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviderRegistry(worker_model),
        AcceptingAgentValidator(),
        AgentFactory(registry),
    )
    result = (
        await WorkflowGraphFactory(agent_runner=generated_agent_runner)
        .create(validated)
        .ainvoke(
            {
                "feed_url": "https://example.com/feed.xml",
                "request": "Résume les principaux thèmes des dernières publications.",
            }
        )
    )

    assert designed_data["nodes"][1]["config"]["source"]["agent"]["tools"] == ["rss_reader"]
    assert result.executed_nodes == ("input", "rss_agent", "output")
    assert result.output == {
        "result": (
            "Les thèmes principaux sont les modèles ouverts, l'utilisation d'outils "
            "et l'observabilité des agents en production."
        )
    }
