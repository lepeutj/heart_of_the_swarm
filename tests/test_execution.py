from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.execution import AgentExecutor
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.repository import Repository
from heart_of_the_swarm.spec import AgentSpec, ModelConfig
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import create_default_registry


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeModel":
        return self


class FakeProviders:
    def __init__(self, model: ToolCapableFakeModel) -> None:
        self.model = model

    def create_model(self, config: ModelConfig) -> ToolCapableFakeModel:
        return self.model


class FailingProviders:
    def create_model(self, config: ModelConfig) -> ToolCapableFakeModel:
        raise RuntimeError("provider unavailable")


class AcceptingValidator:
    async def validate(self, spec: AgentSpec) -> None:
        return None

    def validate_execution(self, spec: AgentSpec) -> None:
        return None


async def test_executor_completes_a_claimed_run() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    database = Database(settings.database_url)
    await database.create_schema()
    registry = create_default_registry()
    spec = AgentSpec(
        name="TestAgent",
        goal="Return a test response",
        tools=[],
        instructions="Answer directly.",
        model={"provider": "test", "model_id": "fake"},
    )
    try:
        async with database.session() as session:
            repository = Repository(session)
            agent = await repository.create_agent(spec, "1", "System prompt")
            queued = await repository.queue_run(agent, "trace-1", "Test input")
            claimed = await repository.claim_next_run("worker-1", 60)
        assert claimed == queued.run_id

        executor = AgentExecutor(
            settings,
            database,
            FakeProviders(ToolCapableFakeModel(responses=[AIMessage(content="Done")])),
            AcceptingValidator(),
            AgentFactory(registry),
            Telemetry(settings),
        )
        await executor.execute(queued.run_id, "worker-1")

        async with database.session() as session:
            completed = await Repository(session).get_run(queued.run_id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.output == "Done"
    finally:
        await database.close()


async def test_executor_failure_is_persisted_with_an_event() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    database = Database(settings.database_url)
    await database.create_schema()
    registry = create_default_registry()
    spec = AgentSpec(
        name="FailingAgent",
        goal="Exercise failure handling",
        tools=[],
        instructions="Answer directly.",
        model={"provider": "test", "model_id": "fake"},
    )
    try:
        async with database.session() as session:
            repository = Repository(session)
            agent = await repository.create_agent(spec, "1", "System prompt")
            queued = await repository.queue_run(agent, "trace-failure", "Test input")
            await repository.claim_next_run("worker-1", 60)

        executor = AgentExecutor(
            settings,
            database,
            FailingProviders(),
            AcceptingValidator(),
            AgentFactory(registry),
            Telemetry(settings),
        )
        await executor.execute(queued.run_id, "worker-1")

        async with database.session() as session:
            repository = Repository(session)
            failed = await repository.get_run(queued.run_id)
            events = await repository.list_run_events(queued.run_id)
        assert failed is not None
        assert failed.status == "failed"
        assert "provider unavailable" in (failed.error or "")
        assert events[-1].event_type == "failed"
    finally:
        await database.close()
