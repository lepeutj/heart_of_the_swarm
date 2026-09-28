from datetime import UTC, datetime

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.observability import ModelUsageEvent, TrajectoryEvent
from heart_of_the_swarm.repository import Repository
from heart_of_the_swarm.spec import AgentSpec


def make_spec(name: str = "ResearchAgent") -> AgentSpec:
    return AgentSpec(
        name=name,
        goal="Research a subject",
        tools=["web_search"],
        instructions="Search and summarize reliable sources.",
        model={"provider": "test", "model_id": "test-model"},
    )


async def test_repository_persists_versions_runs_and_usage() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            agent = await Repository(session).create_agent(make_spec(), "1", "System prompt")
        async with database.session() as session:
            updated = await Repository(session).add_version(
                agent.id, make_spec("UpdatedAgent"), "1", "Updated system prompt"
            )
        assert updated is not None
        assert updated.version == 2
        assert updated.system_prompt == "Updated system prompt"

        async with database.session() as session:
            repository = Repository(session)
            queued = await repository.queue_run(updated, "trace-1", "Research this")
            assert queued.status == "queued"
            run_id = await repository.claim_next_run("worker-1", 60)
            assert run_id == queued.run_id
            await repository.finish_run(queued.run_id, "Done")
            await repository.add_usage(
                [
                    ModelUsageEvent(
                        stage="agent",
                        provider="test",
                        model_id="test-model",
                        input_tokens=10,
                        output_tokens=5,
                        total_tokens=15,
                        cost=0.01,
                    )
                ],
                "trace-1",
                queued.run_id,
            )
        async with database.session() as session:
            repository = Repository(session)
            run = await repository.get_run(queued.run_id)
            events = await repository.list_run_events(queued.run_id)
            summary = await repository.usage_summary()
        assert run is not None
        assert run.status == "completed"
        assert run.output == "Done"
        assert [event.event_type for event in events] == ["queued", "claimed", "completed"]
        assert summary[0].calls == 1
        assert summary[0].total_tokens == 15
        assert summary[0].cost == 0.01
    finally:
        await database.close()


async def test_queued_run_can_be_cancelled_before_claim() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = Repository(session)
            agent = await repository.create_agent(make_spec(), "1", "System prompt")
            queued = await repository.queue_run(agent, "trace-2", "Research this")
            cancelled = await repository.request_cancel(queued.run_id)
            claimed = await repository.claim_next_run("worker-1", 60)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert claimed is None
    finally:
        await database.close()


async def test_expired_worker_lease_requeues_the_run() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = Repository(session)
            agent = await repository.create_agent(make_spec(), "1", "System prompt")
            queued = await repository.queue_run(agent, "trace-3", "Research this")
            await repository.claim_next_run("worker-1", -1)
            recovered = await repository.recover_expired_runs(max_attempts=3)
            run = await repository.get_run(queued.run_id)
            events = await repository.list_run_events(queued.run_id)
        assert recovered == 1
        assert run is not None
        assert run.status == "queued"
        assert events[-1].event_type == "requeued"
    finally:
        await database.close()


async def test_trajectory_steps_are_separated_by_attempt() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = Repository(session)
            agent = await repository.create_agent(make_spec(), "1", "System prompt")
            queued = await repository.queue_run(agent, "trace-4", "Research this")
            first = TrajectoryEvent(
                sequence=1,
                event_type="model.failed",
                component="test-model",
                langchain_run_id="model-run-1",
                parent_run_id=None,
                payload={"error_type": "TimeoutError"},
                created_at=datetime.now(UTC),
            )
            second = TrajectoryEvent(
                sequence=1,
                event_type="model.completed",
                component="test-model",
                langchain_run_id="model-run-2",
                parent_run_id=None,
                payload={"generations": []},
                created_at=datetime.now(UTC),
            )
            await repository.add_trajectory_steps(queued.run_id, 1, [first])
            await repository.add_trajectory_steps(queued.run_id, 2, [second])
            trajectory = await repository.list_trajectory_steps(queued.run_id)

        assert [(step.attempt, step.sequence) for step in trajectory] == [(1, 1), (2, 1)]
        assert [step.event_type for step in trajectory] == ["model.failed", "model.completed"]
    finally:
        await database.close()
