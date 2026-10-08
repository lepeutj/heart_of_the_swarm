from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.execution import RunService
from heart_of_the_swarm.mcp_service import MCPServerService
from heart_of_the_swarm.observability import ModelUsageEvent, TrajectoryEvent
from heart_of_the_swarm.repositories import (
    AgentRepository,
    ObservabilityRepository,
    RunRepository,
    WorkflowRepository,
    WorkflowRevisionConflict,
)
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import MCPServerCreate
from heart_of_the_swarm.workflow_agent_versions import DatabaseAgentVersionResolver
from heart_of_the_swarm.workflows import WorkflowSpec
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave


def make_spec(name: str = "ResearchAgent") -> AgentSpec:
    return AgentSpec(
        name=name,
        goal="Research a subject",
        tools=["web_search"],
        instructions="Search and summarize reliable sources.",
        model={"provider": "test", "model_id": "test-model"},
    )


def make_workflow_draft(expected_revision: int | None = None) -> WorkflowDraftSave:
    workflow = WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "Saved workflow",
            "description": "Editable workflow",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "string"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.request"},
                },
            ],
            "edges": [{"source": "input", "target": "output"}],
        }
    )
    return WorkflowDraftSave(
        spec=workflow.model_dump(mode="json"),
        editor={
            "positions": {
                "input": {"x": 80, "y": 160},
                "output": {"x": 520, "y": 160},
            },
            "viewport": {"x": 10, "y": 20, "zoom": 1.25},
        },
        expected_revision=expected_revision,
    )


class AcceptingExecutionValidator:
    def validate_execution(self, _spec: AgentSpec) -> None:
        pass


async def test_run_service_queues_the_selected_immutable_agent_version() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = AgentRepository(session)
            first = await repository.create(make_spec(), "1", "First prompt")
            second = await repository.add_version(
                first.id,
                make_spec("UpdatedAgent"),
                "1",
                "Second prompt",
            )
        assert second is not None

        queued = await RunService(database, AcceptingExecutionValidator()).queue(
            first.version_id,
            "Use the authorized version",
            "trace-immutable-version",
        )

        assert queued.agent_version == first.version
        assert queued.agent_version != second.version
    finally:
        await database.close()


async def test_mcp_sources_are_persisted_for_other_processes() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    first_process = MCPServerService(database)
    second_process = MCPServerService(database)
    try:
        created = await first_process.create(
            MCPServerCreate(name="github", url="https://mcp.example.test/tools")
        )

        assert await second_process.enabled_sources() == {
            "github": "https://mcp.example.test/tools"
        }
        loaded = await second_process.get(str(created.id))
        assert loaded is not None
        assert (loaded.id, loaded.name, loaded.url, loaded.enabled) == (
            created.id,
            created.name,
            created.url,
            created.enabled,
        )
        await second_process.seed({"github": "https://seed.example.test/tools"})
        assert await first_process.enabled_sources() == {"github": "https://mcp.example.test/tools"}
        with pytest.raises(ValueError, match="already exists"):
            await second_process.create(
                MCPServerCreate(name="github", url="https://other.example.test/tools")
            )
    finally:
        await database.close()


async def test_repository_persists_versions_runs_and_usage() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            agent = await AgentRepository(session).create(make_spec(), "1", "System prompt")
        async with database.session() as session:
            updated = await AgentRepository(session).add_version(
                agent.id, make_spec("UpdatedAgent"), "1", "Updated system prompt"
            )
        assert updated is not None
        assert updated.version == 2
        assert updated.version_id != agent.version_id
        assert updated.system_prompt == "Updated system prompt"

        resolved = await DatabaseAgentVersionResolver(database).resolve(UUID(updated.version_id))
        assert resolved is not None
        assert resolved.spec == updated.spec
        assert resolved.system_prompt == "Updated system prompt"

        async with database.session() as session:
            repository = RunRepository(session)
            queued = await repository.queue(updated, "trace-1", "Research this")
            assert queued.status == "queued"
            run_id = await repository.claim_next("worker-1", 60)
            assert run_id == queued.run_id
            await repository.finish(queued.run_id, "Done")
            await ObservabilityRepository(session).add_usage(
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
            run = await RunRepository(session).get(queued.run_id)
            events = await RunRepository(session).list_events(queued.run_id)
            summary = await ObservabilityRepository(session).usage_summary()
        assert run is not None
        assert run.status == "completed"
        assert run.output == "Done"
        assert [event.event_type for event in events] == ["queued", "claimed", "completed"]
        assert summary[0].calls == 1
        assert summary[0].total_tokens == 15
        assert summary[0].cost == 0.01
    finally:
        await database.close()


async def test_queue_persists_agent_run_before_its_foreign_keyed_event() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys = ON"))
    try:
        async with database.session() as session:
            agent = await AgentRepository(session).create(make_spec(), "1", "System prompt")
            queued = await RunRepository(session).queue(agent, "trace-foreign-key", "Research this")

        async with database.session() as session:
            events = await RunRepository(session).list_events(queued.run_id)

        assert [event.event_type for event in events] == ["queued"]
    finally:
        await database.close()


async def test_queued_run_can_be_cancelled_before_claim() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            agent = await AgentRepository(session).create(make_spec(), "1", "System prompt")
            repository = RunRepository(session)
            queued = await repository.queue(agent, "trace-2", "Research this")
            cancelled = await repository.request_cancel(queued.run_id)
            claimed = await repository.claim_next("worker-1", 60)
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
            agent = await AgentRepository(session).create(make_spec(), "1", "System prompt")
            repository = RunRepository(session)
            queued = await repository.queue(agent, "trace-3", "Research this")
            await repository.claim_next("worker-1", -1)
            recovered = await repository.recover_expired(max_attempts=3)
            run = await repository.get(queued.run_id)
            events = await repository.list_events(queued.run_id)
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
            agent = await AgentRepository(session).create(make_spec(), "1", "System prompt")
            queued = await RunRepository(session).queue(agent, "trace-4", "Research this")
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
            observability = ObservabilityRepository(session)
            await observability.add_run_observability(queued.run_id, 1, "trace-4", [], [first])
            await observability.add_run_observability(queued.run_id, 2, "trace-4", [], [second])
            trajectory = await observability.list_trajectory(queued.run_id)

        assert [(step.attempt, step.sequence) for step in trajectory] == [(1, 1), (2, 1)]
        assert [step.event_type for step in trajectory] == ["model.failed", "model.completed"]
    finally:
        await database.close()


async def test_workflow_draft_reopens_with_layout_and_creates_immutable_version() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            repository = WorkflowRepository(session)
            created = await repository.save(make_workflow_draft())
            version = await repository.create_version(str(created.id))
        async with database.session() as session:
            reopened = await WorkflowRepository(session).get(str(created.id))

        assert created.revision == 1
        assert version is not None
        assert version.version == 1
        assert reopened is not None
        assert reopened.editor.positions["input"].x == 80
        assert reopened.editor.viewport.zoom == 1.25
        assert reopened.latest_version == 1
    finally:
        await database.close()


async def test_workflow_draft_update_requires_current_revision() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            await WorkflowRepository(session).save(make_workflow_draft())
        async with database.session() as session:
            updated = await WorkflowRepository(session).save(make_workflow_draft(1))
        async with database.session() as session:
            with pytest.raises(WorkflowRevisionConflict):
                await WorkflowRepository(session).save(make_workflow_draft(1))

        assert updated.revision == 2
    finally:
        await database.close()
