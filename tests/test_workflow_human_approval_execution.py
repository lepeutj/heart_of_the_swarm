from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.models import WorkflowInterruptionRecord, WorkflowRunRecord
from heart_of_the_swarm.repositories import (
    WorkflowInterruptionRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflow_approvals import WorkflowApprovalService
from heart_of_the_swarm.workflow_execution import WorkflowExecutor, WorkflowRunService
from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    WorkflowExecutionEvent,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
    WorkflowVersionRunner,
)
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftSave,
    WorkflowVersionSnapshot,
)
from heart_of_the_swarm.workflows.execution.checkpoints import DurableWorkflowCheckpoints

WORKFLOW_ID = UUID("2089e424-7158-488c-8cb2-1f4c9bca2fd3")
VERSION_ID = UUID("f05de0e5-9b65-4f89-9938-fe9844d43821")


def approval_spec() -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "2",
            "id": str(WORKFLOW_ID),
            "name": "Checkpoint-backed approval",
            "description": "Prepare data and wait for one operator decision.",
            "input_schema": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
            "output_schema": {"type": "boolean"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "prepare",
                    "type": "transform",
                    "name": "Prepare",
                    "config": {"assign": {"$.prepared": True}},
                },
                {
                    "id": "approve",
                    "type": "human_approval",
                    "name": "Approve",
                    "config": {
                        "prompt": "Approve the prepared result?",
                        "output": {"to_state": "$.approved"},
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.approved"},
                },
            ],
            "edges": [
                {"source": "input", "target": "prepare"},
                {"source": "prepare", "target": "approve"},
                {"source": "approve", "target": "output"},
            ],
        }
    )


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[WorkflowExecutionEvent] = []

    async def emit(self, event: WorkflowExecutionEvent) -> None:
        self.events.append(event)


async def test_runner_surfaces_minimal_checkpoint_backed_approval() -> None:
    tools = ToolRegistry()
    runner = WorkflowVersionRunner(
        WorkflowValidator([], []),
        tools,
        WorkflowGraphFactory(capabilities=tools),
    )
    version = WorkflowVersionSnapshot(
        id=VERSION_ID,
        workflow_id=WORKFLOW_ID,
        version=1,
        spec=approval_spec(),
    )
    sink = RecordingSink()

    result = await runner.run(
        version,
        {"request": "publish"},
        ExecutionPolicy(timeout_seconds=2, recursion_limit=20),
        sink,
        execution_id="approval-run",
        thread_id="approval-thread",
        checkpointer=InMemorySaver(),
    )

    assert result.interrupted is True
    assert result.checkpoint_id is not None
    assert result.executed_nodes == ("input", "prepare")
    assert result.state == {"request": "publish", "prepared": True}
    assert "approved" not in result.state
    assert result.interruption is not None
    assert result.interruption.kind == "approval"
    assert result.interruption.node_id == "approve"
    assert result.interruption.prompt == "Approve the prepared result?"
    assert result.interruption.response_schema["properties"]["approved"]["type"] == "boolean"
    assert sink.events[-1].event_type == "checkpoint.created"
    assert [event.event_type for event in sink.events].count("node.started") == 3
    assert [event.event_type for event in sink.events].count("node.completed") == 2


async def test_worker_persists_pending_approval_and_releases_run() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    checkpoints = DurableWorkflowCheckpoints("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    await checkpoints.start()
    tools = ToolRegistry()
    validator = WorkflowValidator([], [])
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    try:
        async with database.session() as session:
            workflows = WorkflowRepository(session)
            draft = await workflows.save(
                WorkflowDraftSave(spec=approval_spec().model_dump(mode="json"))
            )
            version = await workflows.create_version(str(draft.id))
        assert version is not None

        service = WorkflowRunService(database, validator, tools, checkpoints)
        queued = await service.queue(version.id, {"request": "publish"}, "trace-approval")
        async with database.session() as session:
            claimed = await WorkflowRunRepository(session).claim_next("worker", 60)
        assert claimed == str(queued.run_id)

        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
            checkpoints=checkpoints,
        )
        await executor.execute(str(queued.run_id), "worker")

        run = await service.get(queued.run_id)
        events = await service.events(queued.run_id)
        async with database.session() as session:
            interruption = (
                await session.execute(
                    select(WorkflowInterruptionRecord).where(
                        WorkflowInterruptionRecord.workflow_run_id == str(queued.run_id)
                    )
                )
            ).scalar_one()
            with pytest.raises(ValueError, match="not running"):
                await WorkflowInterruptionRepository(session).create_approval(
                    str(queued.run_id),
                    "approve",
                    interruption.checkpoint_id,
                )
            persisted = (
                (
                    await session.execute(
                        select(WorkflowInterruptionRecord).where(
                            WorkflowInterruptionRecord.workflow_run_id == str(queued.run_id)
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert run is not None
        assert run.status == "interrupted"
        assert run.output is None
        assert run.resume_checkpoint_id == interruption.checkpoint_id
        assert interruption.status == "pending"
        assert interruption.node_id == "approve"
        assert len(persisted) == 1
        approvals = WorkflowApprovalService(database, checkpoints)
        pending = await approvals.list()
        assert [item.id for item in pending] == [UUID(interruption.id)]
        assert await approvals.get(UUID(interruption.id)) == pending[0]
        assert events is not None
        assert [event.event_type for event in events][-3:] == [
            "checkpoint.created",
            "approval.requested",
            "workflow.interrupted",
        ]
    finally:
        await checkpoints.close()
        await database.close()


@pytest.mark.parametrize("approved", [True, False])
async def test_approval_response_resumes_in_new_run_without_replaying_prior_nodes(
    approved: bool,
) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    checkpoints = DurableWorkflowCheckpoints("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    await checkpoints.start()
    tools = ToolRegistry()
    validator = WorkflowValidator([], [])
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    try:
        async with database.session() as session:
            workflows = WorkflowRepository(session)
            draft = await workflows.save(
                WorkflowDraftSave(spec=approval_spec().model_dump(mode="json"))
            )
            version = await workflows.create_version(str(draft.id))
        assert version is not None

        runs = WorkflowRunService(database, validator, tools, checkpoints)
        first = await runs.queue(version.id, {"request": "publish"}, "trace-first")
        async with database.session() as session:
            await WorkflowRunRepository(session).claim_next("worker-1", 60)
        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
            checkpoints=checkpoints,
        )
        await executor.execute(str(first.run_id), "worker-1")

        async with database.session() as session:
            interruption = (
                await session.execute(
                    select(WorkflowInterruptionRecord).where(
                        WorkflowInterruptionRecord.workflow_run_id == str(first.run_id)
                    )
                )
            ).scalar_one()
        with pytest.raises(ValueError, match="explicit response"):
            await runs.resume(first.run_id, interruption.checkpoint_id, "trace-bypass")

        resumed = await WorkflowApprovalService(database, checkpoints).respond(
            UUID(interruption.id),
            {"approved": approved},
            "trace-resumed",
        )
        assert resumed.run_id != first.run_id
        assert resumed.thread_id == first.thread_id
        assert resumed.resumed_from_run_id == first.run_id
        assert resumed.resume_checkpoint_id == interruption.checkpoint_id
        assert resumed.attempt_index == 2
        with pytest.raises(ValueError, match="not pending"):
            await WorkflowApprovalService(database, checkpoints).respond(
                UUID(interruption.id),
                {"approved": not approved},
                "trace-duplicate",
            )

        async with database.session() as session:
            claimed = await WorkflowRunRepository(session).claim_next("worker-2", 60)
        assert claimed == str(resumed.run_id)
        await executor.execute(str(resumed.run_id), "worker-2")

        completed = await runs.get(resumed.run_id)
        first_events = await runs.events(first.run_id)
        resumed_events = await runs.events(resumed.run_id)
        async with database.session() as session:
            resolved = await WorkflowInterruptionRepository(session).get(interruption.id)

        assert completed is not None
        assert completed.status == "completed"
        assert completed.output is approved
        assert resolved is not None
        assert resolved.status == "resolved"
        assert resolved.response == {"approved": approved}
        assert first_events is not None
        assert first_events[-1].event_type == "approval.resolved"
        assert resumed_events is not None
        assert resumed_events[0].event_type == "workflow.resumed"
        resumed_node_ids = [
            event.data["node_id"] for event in resumed_events if event.event_type == "node.started"
        ]
        assert resumed_node_ids == ["approve", "output"]
        assert resumed_events[-1].event_type == "workflow.completed"
    finally:
        await checkpoints.close()
        await database.close()


async def test_invalid_approval_response_creates_no_resumed_run() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    checkpoints = DurableWorkflowCheckpoints("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    await checkpoints.start()
    tools = ToolRegistry()
    validator = WorkflowValidator([], [])
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    try:
        async with database.session() as session:
            workflows = WorkflowRepository(session)
            draft = await workflows.save(
                WorkflowDraftSave(spec=approval_spec().model_dump(mode="json"))
            )
            version = await workflows.create_version(str(draft.id))
        assert version is not None

        runs = WorkflowRunService(database, validator, tools, checkpoints)
        first = await runs.queue(version.id, {"request": "publish"}, "trace-first")
        async with database.session() as session:
            await WorkflowRunRepository(session).claim_next("worker-1", 60)
        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
            checkpoints=checkpoints,
        )
        await executor.execute(str(first.run_id), "worker-1")

        async with database.session() as session:
            interruption = (
                await session.execute(
                    select(WorkflowInterruptionRecord).where(
                        WorkflowInterruptionRecord.workflow_run_id == str(first.run_id)
                    )
                )
            ).scalar_one()
            before = (
                await session.execute(select(func.count()).select_from(WorkflowRunRecord))
            ).scalar_one()

        with pytest.raises(ValueError, match="response is invalid"):
            await WorkflowApprovalService(database, checkpoints).respond(
                UUID(interruption.id),
                {"approved": "yes"},
                "trace-invalid",
            )

        async with database.session() as session:
            after = (
                await session.execute(select(func.count()).select_from(WorkflowRunRecord))
            ).scalar_one()
            unchanged = await WorkflowInterruptionRepository(session).get(interruption.id)
        assert after == before
        assert unchanged is not None
        assert unchanged.status == "pending"
        assert unchanged.response is None
    finally:
        await checkpoints.close()
        await database.close()
