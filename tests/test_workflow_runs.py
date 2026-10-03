import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.tools import tool

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository, WorkflowRunRepository
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflow_execution import WorkflowExecutor, WorkflowRunService
from heart_of_the_swarm.workflows import (
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowRuntimePolicy,
    WorkflowValidator,
    WorkflowVersionRunner,
)
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftSave,
    WorkflowVersionDetail,
)
from heart_of_the_swarm.workflows.execution import WorkflowExecutionEvent
from heart_of_the_swarm.workflows.spec import WorkflowSpec


@tool
async def slow_echo(value: str) -> dict[str, str]:
    """Return a value after enough delay to exercise the workflow timeout."""
    await asyncio.sleep(0.2)
    return {"result": value}


@tool
async def changed_echo(message: str) -> dict[str, str]:
    """Represent an incompatible remote capability schema."""
    return {"result": message}


def workflow_draft(*, connector: bool = False) -> WorkflowDraftSave:
    middle: list[dict[str, Any]] = [
        {
            "id": "copy",
            "type": "transform",
            "name": "Copy request",
            "config": {"assign": {"$.answer": {"from_state": "$.request"}}},
        }
    ]
    if connector:
        middle = [
            {
                "id": "slow",
                "type": "connector",
                "name": "Slow echo",
                "config": {
                    "capability_id": "slow_echo",
                    "inputs": {"value": {"from_state": "$.request"}},
                    "outputs": {"result": {"to_state": "$.answer"}},
                },
            }
        ]
    middle_id = middle[0]["id"]
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
                "name": "Durable workflow",
                "description": "Exercise the durable workflow queue.",
                "input_schema": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
                "output_schema": {"type": "string"},
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    *middle,
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"output_path": "$.answer"},
                    },
                ],
                "edges": [
                    {"source": "input", "target": middle_id},
                    {"source": middle_id, "target": "output"},
                ],
            },
            "editor": {},
        }
    )


async def published_version(
    database: Database,
    tools: ToolRegistry,
    *,
    connector: bool = False,
):
    async with database.session() as session:
        repository = WorkflowRepository(session)
        draft = await repository.save(workflow_draft(connector=connector))
        contracts = [tools.contract("slow_echo")] if connector else []
        version = await repository.create_version(
            str(draft.id),
            contracts,
            expected_revision=draft.revision,
        )
    assert version is not None
    return version


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[WorkflowExecutionEvent] = []

    async def emit(self, event: WorkflowExecutionEvent) -> None:
        self.events.append(event)


def standalone_version() -> WorkflowVersionDetail:
    draft = workflow_draft()
    spec = WorkflowSpec.model_validate(draft.spec)
    return WorkflowVersionDetail(
        id=uuid4(),
        workflow_id=spec.id,
        version=1,
        spec=spec,
        editor=draft.editor,
        created_at=datetime.now(UTC),
    )


async def test_version_runner_executes_loaded_version_without_persistence() -> None:
    tools = ToolRegistry()
    validator = WorkflowValidator(lambda: tools.names, [])
    sink = RecordingEventSink()
    runner = WorkflowVersionRunner(
        validator,
        tools,
        WorkflowGraphFactory(capabilities=tools),
    )

    result = await runner.run(
        standalone_version(),
        {"request": "portable"},
        WorkflowRuntimePolicy(timeout_seconds=1, recursion_limit=20),
        sink,
        execution_id="standalone-run",
    )

    assert result.output == "portable"
    assert result.executed_nodes == ("input", "copy", "output")
    assert [event.event_type for event in sink.events] == [
        "node.started",
        "node.completed",
        "node.started",
        "node.completed",
        "node.started",
        "node.completed",
    ]
    assert {event.data["workflow_run_id"] for event in sink.events} == {"standalone-run"}


async def test_invalid_input_is_rejected_before_a_run_is_queued() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    tools = ToolRegistry()
    validator = WorkflowValidator(lambda: tools.names, [])
    try:
        version = await published_version(database, tools)
        service = WorkflowRunService(database, validator, tools)

        with pytest.raises(WorkflowExecutionError, match="Workflow input is invalid"):
            await service.queue(version.id, {}, "trace-invalid-input")

        async with database.session() as session:
            assert await WorkflowRunRepository(session).claim_next("worker-1", 60) is None
    finally:
        await database.close()


async def test_workflow_run_executes_version_and_persists_node_events() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    tools = ToolRegistry()
    validator = WorkflowValidator(lambda: tools.names, [])
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    try:
        version = await published_version(database, tools)
        service = WorkflowRunService(database, validator, tools)
        queued = await service.queue(version.id, {"request": "hello"}, "trace-workflow")
        async with database.session() as session:
            claimed = await WorkflowRunRepository(session).claim_next("worker-1", 60)
        assert claimed == str(queued.run_id)

        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
        )
        await executor.execute(str(queued.run_id), "worker-1")

        run = await service.get(queued.run_id)
        events = await service.events(queued.run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.output == "hello"
        assert events is not None
        assert [event.sequence for event in events] == list(range(1, 10))
        assert [event.event_type for event in events] == [
            "workflow.queued",
            "workflow.started",
            "node.started",
            "node.completed",
            "node.started",
            "node.completed",
            "node.started",
            "node.completed",
            "workflow.completed",
        ]
        assert [
            event.data.get("node_id") for event in events if event.event_type == "node.completed"
        ] == ["input", "copy", "output"]
    finally:
        await database.close()


async def test_workflow_timeout_is_terminal_and_marks_active_node_failed() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    tools = ToolRegistry([slow_echo])
    validator = WorkflowValidator(lambda: tools.names, [])
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        workflow_timeout_seconds=0.05,
    )
    try:
        version = await published_version(database, tools, connector=True)
        service = WorkflowRunService(database, validator, tools)
        queued = await service.queue(version.id, {"request": "hello"}, "trace-timeout")
        async with database.session() as session:
            await WorkflowRunRepository(session).claim_next("worker-1", 60)

        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
        )
        await executor.execute(str(queued.run_id), "worker-1")

        run = await service.get(queued.run_id)
        events = await service.events(queued.run_id)
        assert run is not None
        assert run.status == "timed_out"
        assert events is not None
        assert any(
            event.event_type == "node.failed" and event.data.get("node_id") == "slow"
            for event in events
        )
        assert events[-1].event_type == "workflow.timed_out"
    finally:
        await database.close()


async def test_changed_capability_contract_is_rejected_before_queueing() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    original_tools = ToolRegistry([slow_echo])
    try:
        version = await published_version(database, original_tools, connector=True)
        incompatible = changed_echo.model_copy(update={"name": "slow_echo"})
        current_tools = ToolRegistry([incompatible])
        service = WorkflowRunService(
            database,
            WorkflowValidator(lambda: current_tools.names, []),
            current_tools,
        )

        with pytest.raises(ValueError, match="capability contract changed"):
            await service.queue(version.id, {"request": "hello"}, "trace-incompatible")
    finally:
        await database.close()


async def test_expired_workflow_run_fails_without_replaying_connector_nodes() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    tools = ToolRegistry([slow_echo])
    validator = WorkflowValidator(lambda: tools.names, [])
    try:
        version = await published_version(database, tools, connector=True)
        service = WorkflowRunService(database, validator, tools)
        queued = await service.queue(version.id, {"request": "hello"}, "trace-expired")
        async with database.session() as session:
            repository = WorkflowRunRepository(session)
            await repository.claim_next("worker-1", -1)
            assert await repository.recover_expired() == 1

        run = await service.get(queued.run_id)
        events = await service.events(queued.run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.attempt == 1
        assert events is not None
        assert events[-1].event_type == "workflow.failed"
        assert events[-1].data == {"reason": "worker_lease_expired"}
    finally:
        await database.close()
