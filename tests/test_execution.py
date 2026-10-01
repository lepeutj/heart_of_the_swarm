from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.execution import AgentExecutor
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.repositories import AgentRepository, ObservabilityRepository, RunRepository
from heart_of_the_swarm.spec import AgentSpec, ModelConfig
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import create_default_registry


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


class FailingFakeModel(ToolCapableFakeModel):
    def _generate(self, *args: Any, **kwargs: Any):
        raise RuntimeError("model unavailable")


class FakeProviders:
    def __init__(self, model: ToolCapableFakeModel) -> None:
        self.model = model

    def create_model(self, config: ModelConfig) -> ToolCapableFakeModel:
        return self.model


class AcceptingValidator:
    async def validate(self, spec: AgentSpec) -> None:
        return None

    def validate_execution(self, spec: AgentSpec) -> None:
        return None


async def execute_model(
    model: ToolCapableFakeModel, *, tools: list[str] | None = None, trace_id: str = "trace-1"
) -> tuple[Database, str]:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    database = Database(settings.database_url)
    await database.create_schema()
    registry = create_default_registry()
    spec = AgentSpec(
        name="TestAgent",
        goal="Return a test response",
        tools=tools or [],
        instructions="Answer directly.",
        model={"provider": "test", "model_id": "fake"},
    )
    async with database.session() as session:
        agent = await AgentRepository(session).create(spec, "1", "System prompt")
        queued = await RunRepository(session).queue(agent, trace_id, "Test input")
        claimed = await RunRepository(session).claim_next("worker-1", 60)
    assert claimed == queued.run_id

    executor = AgentExecutor(
        settings,
        database,
        AgentRunner(FakeProviders(model), AcceptingValidator(), AgentFactory(registry)),
        Telemetry(settings),
    )
    await executor.execute(queued.run_id, "worker-1")
    return database, queued.run_id


async def test_direct_model_response_trajectory() -> None:
    database, run_id = await execute_model(
        ToolCapableFakeModel(
            responses=[
                AIMessage(
                    content="Done",
                    response_metadata={"model_name": "fake-v1", "finish_reason": "stop"},
                )
            ]
        )
    )
    try:
        async with database.session() as session:
            trajectory = await ObservabilityRepository(session).list_trajectory(run_id)
        assert [step.event_type for step in trajectory] == ["model.started", "model.completed"]
        assert trajectory[0].payload["messages"][0][-1]["content"] == "Test input"
        message = trajectory[1].payload["generations"][0][0]["message"]
        assert message["type"] == "ai"
        assert message["content"] == "Done"
        assert message["response_metadata"] == {
            "model_name": "fake-v1",
            "finish_reason": "stop",
        }
    finally:
        await database.close()


async def test_executor_completes_a_claimed_run() -> None:
    database, run_id = await execute_model(
        ToolCapableFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "calculator",
                            "args": {"expression": "1 + 1"},
                            "id": "calculator-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Done"),
            ]
        ),
        tools=["calculator"],
    )
    try:
        async with database.session() as session:
            completed = await RunRepository(session).get(run_id)
            trajectory = await ObservabilityRepository(session).list_trajectory(run_id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.output == "Done"
        assert [step.event_type for step in trajectory] == [
            "model.started",
            "model.completed",
            "tool.started",
            "tool.completed",
            "model.started",
            "model.completed",
        ]
        assert trajectory[0].payload["messages"][0][-1]["content"] == "Test input"
        assert trajectory[1].payload["generations"][0][0]["message"]["tool_calls"][0]["args"] == {
            "expression": "1 + 1"
        }
        assert trajectory[2].component == "calculator"
        assert trajectory[2].payload["input"] == {"expression": "1 + 1"}
        assert trajectory[3].payload["output"]["content"] == "2"
        assert trajectory[5].payload["generations"][0][0]["message"]["content"] == "Done"
    finally:
        await database.close()


async def test_executor_failure_is_persisted_with_an_event() -> None:
    database, run_id = await execute_model(
        FailingFakeModel(responses=[AIMessage(content="unused")]), trace_id="trace-failure"
    )
    try:
        async with database.session() as session:
            failed = await RunRepository(session).get(run_id)
            events = await RunRepository(session).list_events(run_id)
            trajectory = await ObservabilityRepository(session).list_trajectory(run_id)
        assert failed is not None
        assert failed.status == "failed"
        assert "model unavailable" in (failed.error or "")
        assert events[-1].event_type == "failed"
        assert [step.event_type for step in trajectory] == ["model.started", "model.failed"]
        assert trajectory[-1].payload["error_message"] == "model unavailable"
    finally:
        await database.close()


async def test_failed_tool_call_is_captured() -> None:
    database, run_id = await execute_model(
        ToolCapableFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "calculator",
                            "args": {"expression": "not arithmetic"},
                            "id": "calculator-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Recovered"),
            ]
        ),
        tools=["calculator"],
    )
    try:
        async with database.session() as session:
            trajectory = await ObservabilityRepository(session).list_trajectory(run_id)
        failed = next(step for step in trajectory if step.event_type == "tool.failed")
        assert failed.component == "calculator"
        assert failed.payload["error_type"] == "ValueError"
    finally:
        await database.close()
