from uuid import uuid4

import pytest
from pydantic import ValidationError

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository, WorkflowRunRepository
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.triggers import (
    ScheduleTriggerConfig,
    TriggerDetail,
    TriggerDisabledError,
    TriggerSpec,
)
from heart_of_the_swarm.triggers.service import TriggerService
from heart_of_the_swarm.triggers.webhook import WebhookTriggerService
from heart_of_the_swarm.workflow_execution import WorkflowExecutor, WorkflowRunService
from heart_of_the_swarm.workflows import WorkflowExecutionError, WorkflowValidator
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave


def workflow_draft() -> WorkflowDraftSave:
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": str(uuid4()),
                "name": "Triggered workflow",
                "description": "Exercise immutable trigger targets.",
                "input_schema": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"outputs": {"request": {"from_state": "$.request"}}},
                    },
                ],
                "edges": [{"source": "input", "target": "output"}],
            },
            "editor": {},
        }
    )


def test_trigger_configs_are_strictly_typed() -> None:
    target_version_id = uuid4()

    schedule = TriggerSpec.model_validate(
        {
            "name": "Every ten minutes",
            "type": "schedule",
            "target_type": "workflow",
            "target_version_id": target_version_id,
            "config": {"every_seconds": 600},
        }
    )
    assert schedule.config == ScheduleTriggerConfig(every_seconds=600)

    with pytest.raises(ValidationError):
        TriggerSpec.model_validate(
            {
                "name": "Invalid webhook",
                "type": "webhook",
                "target_type": "workflow",
                "target_version_id": target_version_id,
                "config": {"every_seconds": 600},
            }
        )


async def test_trigger_persists_an_exact_immutable_workflow_version() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            workflows = WorkflowRepository(session)
            draft = await workflows.save(workflow_draft())
            version_one = await workflows.create_version(
                str(draft.id),
                [],
                expected_revision=draft.revision,
            )
        assert version_one is not None

        service = TriggerService(database)
        created = await service.create(
            TriggerSpec.model_validate(
                {
                    "name": "Scheduled report",
                    "type": "schedule",
                    "target_type": "workflow",
                    "target_version_id": version_one.id,
                    "config": {"every_seconds": 600},
                }
            )
        )

        async with database.session() as session:
            workflows = WorkflowRepository(session)
            saved = await workflows.save(
                WorkflowDraftSave(
                    spec=draft.spec,
                    editor=draft.editor,
                    expected_revision=draft.revision,
                )
            )
            version_two = await workflows.create_version(
                str(saved.id),
                [],
                expected_revision=saved.revision,
            )
        assert version_two is not None

        loaded = await service.get(created.id)
        assert loaded is not None
        assert loaded.target_version_id == version_one.id
        assert loaded.target_version_id != version_two.id
        assert loaded.next_fire_at is not None
        assert await service.list() == [loaded]
    finally:
        await database.close()


async def test_trigger_rejects_a_missing_target_version() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        service = TriggerService(database)
        spec = TriggerSpec.model_validate(
            {
                "name": "Missing target",
                "type": "manual",
                "target_type": "workflow",
                "target_version_id": uuid4(),
                "config": {},
            }
        )

        with pytest.raises(ValueError, match="workflow version not found"):
            await service.create(spec)

        assert await service.list() == []
    finally:
        await database.close()


async def webhook_context(
    database: Database,
    *,
    enabled: bool = True,
) -> tuple[TriggerDetail, WorkflowRunService, WebhookTriggerService]:
    tools = ToolRegistry()
    validator = WorkflowValidator(lambda: tools.names, [])
    triggers = TriggerService(database)
    async with database.session() as session:
        workflows = WorkflowRepository(session)
        draft = await workflows.save(workflow_draft())
        version = await workflows.create_version(
            str(draft.id),
            [],
            expected_revision=draft.revision,
        )
    assert version is not None
    trigger = await triggers.create(
        TriggerSpec.model_validate(
            {
                "name": "External event",
                "type": "webhook",
                "target_type": "workflow",
                "target_version_id": version.id,
                "enabled": enabled,
                "config": {},
            }
        )
    )
    runs = WorkflowRunService(database, validator, tools)
    return trigger, runs, WebhookTriggerService(triggers, runs)


async def test_distinct_webhook_events_create_distinct_runs_with_origin() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        trigger, runs, webhook = await webhook_context(database)

        first = await webhook.invoke(trigger.id, {"request": "first"}, "trace-first")
        second = await webhook.invoke(trigger.id, {"request": "second"}, "trace-second")

        assert first.run_id != second.run_id
        assert first.trigger_event_id != second.trigger_event_id
        assert first.trigger_id == second.trigger_id == trigger.id
        first_run = await runs.get(first.run_id)
        second_run = await runs.get(second.run_id)
        assert first_run is not None and first_run.input == {"request": "first"}
        assert second_run is not None and second_run.input == {"request": "second"}

        async with database.session() as session:
            claimed = await WorkflowRunRepository(session).claim_next("worker", 60)
        assert claimed == str(first.run_id)
        tools = ToolRegistry()
        validator = WorkflowValidator(lambda: tools.names, [])
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
        executor = WorkflowExecutor(
            settings,
            database,
            validator,
            tools,
            agent_runner=None,  # type: ignore[arg-type]
            telemetry=Telemetry(settings),
        )
        await executor.execute(claimed, "worker")
        completed = await runs.get(first.run_id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.output == {"request": "first"}
    finally:
        await database.close()


async def test_invalid_webhook_payload_does_not_create_a_partial_run() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        trigger, _runs, webhook = await webhook_context(database)

        with pytest.raises(WorkflowExecutionError, match="Workflow input is invalid"):
            await webhook.invoke(trigger.id, {}, "trace-invalid")

        async with database.session() as session:
            assert await WorkflowRunRepository(session).claim_next("worker", 60) is None
    finally:
        await database.close()


async def test_disabled_webhook_does_not_create_a_run() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        trigger, _runs, webhook = await webhook_context(database, enabled=False)

        with pytest.raises(TriggerDisabledError, match="trigger is disabled"):
            await webhook.invoke(trigger.id, {"request": "ignored"}, "trace-disabled")

        async with database.session() as session:
            assert await WorkflowRunRepository(session).claim_next("worker", 60) is None
    finally:
        await database.close()
