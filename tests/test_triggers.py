from uuid import uuid4

import pytest
from pydantic import ValidationError

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository
from heart_of_the_swarm.triggers import (
    ScheduleTriggerConfig,
    TriggerSpec,
)
from heart_of_the_swarm.triggers.service import TriggerService
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave


def workflow_draft() -> WorkflowDraftSave:
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": str(uuid4()),
                "name": "Triggered workflow",
                "description": "Exercise immutable trigger targets.",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"outputs": {}},
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
