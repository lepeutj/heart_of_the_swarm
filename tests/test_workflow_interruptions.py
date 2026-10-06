import asyncio
from pathlib import Path

import pytest

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.models import ExecutionThreadRecord
from heart_of_the_swarm.repositories import (
    WorkflowInterruptionRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave
from heart_of_the_swarm.workflows.interruptions import (
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import WorkflowRunAccepted


def approval_draft() -> WorkflowDraftSave:
    return WorkflowDraftSave(
        spec={
            "schema_version": "2",
            "id": "73683a0e-6659-4214-8249-af429e732972",
            "name": "Persisted approval workflow",
            "description": "Exercise durable approval persistence.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "boolean"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "approve",
                    "type": "human_approval",
                    "name": "Approve",
                    "config": {
                        "prompt": "Approve this result?",
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
                {"source": "input", "target": "approve"},
                {"source": "approve", "target": "output"},
            ],
        },
    )


async def create_pending_approval(
    database: Database,
) -> WorkflowInterruptionDetail:
    async with database.session() as session:
        workflows = WorkflowRepository(session)
        draft = await workflows.save(approval_draft())
        version = await workflows.create_version(str(draft.id))
        assert version is not None
        runs = WorkflowRunRepository(session)
        queued = await runs.queue(version, "trace-approval", {})
        claimed = await runs.claim_next("worker", 60)
        assert claimed == str(queued.run_id)
        approval = await WorkflowInterruptionRepository(session).create_approval(
            str(queued.run_id),
            "approve",
            "checkpoint-1",
        )
        events = await runs.list_events(str(queued.run_id))
        assert [event.event_type for event in events][-2:] == [
            "approval.requested",
            "workflow.interrupted",
        ]
        return approval


async def test_approval_is_derived_from_the_immutable_version_and_resolved_once() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        approval = await create_pending_approval(database)
        assert approval.prompt == "Approve this result?"
        assert approval.status == WorkflowInterruptionStatus.PENDING
        assert approval.response_schema["properties"]["approved"]["type"] == "boolean"

        async with database.session() as session:
            run = await WorkflowRunRepository(session).get(str(approval.workflow_run_id))
            thread = await session.get(ExecutionThreadRecord, str(approval.thread_id))
        assert run is not None
        assert run.status == "interrupted"
        assert run.resume_checkpoint_id == approval.checkpoint_id
        assert thread is not None
        assert thread.status == "interrupted"

        async with database.session() as session:
            repository = WorkflowInterruptionRepository(session)
            resumed = await repository.resolve_and_resume(
                str(approval.id),
                response={"approved": True},
                trace_id="trace-resolved",
            )
            resolved = await repository.get(str(approval.id))
            with pytest.raises(ValueError, match="not pending"):
                await repository.resolve_and_resume(
                    str(approval.id),
                    response={"approved": False},
                    trace_id="trace-duplicate",
                )

        assert resumed.thread_id == approval.thread_id
        assert resumed.resumed_from_run_id == approval.workflow_run_id
        assert resumed.attempt_index == 2
        assert resolved is not None
        assert resolved.status == WorkflowInterruptionStatus.RESOLVED
        assert resolved.response == {"approved": True}
        assert resolved.resolved_at is not None
    finally:
        await database.close()


async def test_invalid_response_does_not_mutate_pending_approval() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        approval = await create_pending_approval(database)
        async with database.session() as session:
            repository = WorkflowInterruptionRepository(session)
            with pytest.raises(ValueError, match="response is invalid"):
                await repository.resolve_and_resume(
                    str(approval.id),
                    response={"approved": "yes"},
                    trace_id="trace-invalid",
                )
            unchanged = await repository.get(str(approval.id))

        assert unchanged is not None
        assert unchanged.status == WorkflowInterruptionStatus.PENDING
        assert unchanged.response is None
    finally:
        await database.close()


async def test_cancelled_approval_cannot_be_resolved() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        approval = await create_pending_approval(database)
        async with database.session() as session:
            repository = WorkflowInterruptionRepository(session)
            cancelled = await repository.cancel(
                str(approval.id),
                thread_id=str(approval.thread_id),
                checkpoint_id=approval.checkpoint_id,
            )
            with pytest.raises(ValueError, match="not pending"):
                await repository.resolve_and_resume(
                    str(approval.id),
                    response={"approved": True},
                    trace_id="trace-cancelled",
                )

        assert cancelled.status == WorkflowInterruptionStatus.CANCELLED
        assert cancelled.response is None
    finally:
        await database.close()


async def test_concurrent_resolution_accepts_exactly_one_response(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'interruptions.db'}")
    await database.create_schema()
    try:
        approval = await create_pending_approval(database)

        async def resolve(value: bool) -> WorkflowRunAccepted:
            async with database.session() as session:
                return await WorkflowInterruptionRepository(session).resolve_and_resume(
                    str(approval.id),
                    response={"approved": value},
                    trace_id=f"trace-{value}",
                )

        results = await asyncio.gather(resolve(True), resolve(False), return_exceptions=True)
        successes = [item for item in results if isinstance(item, WorkflowRunAccepted)]
        failures = [item for item in results if isinstance(item, ValueError)]

        assert len(successes) == 1
        assert len(failures) == 1
        assert "not pending" in str(failures[0])
    finally:
        await database.close()
