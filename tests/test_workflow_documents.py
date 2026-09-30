from typing import Any
from uuid import UUID

import pytest

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.workflow_service import WorkflowService
from heart_of_the_swarm.workflows import WorkflowValidationError, WorkflowValidator
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave

WORKFLOW_ID = UUID("47d174a8-b35e-4563-bd86-3bc6b5b5947f")


def draft(*, inline_agent: bool = False, valid: bool = True) -> WorkflowDraftSave:
    middle = []
    edges = [{"source": "input", "target": "output"}]
    output_path = "$.request"
    if inline_agent:
        middle = [
            {
                "id": "research",
                "type": "llm",
                "name": "Research",
                "config": {
                    "agent": {
                        "name": "ResearchAgent",
                        "goal": "Research",
                        "instructions": "Use available tools.",
                        "model": {"provider": "test", "model_id": "test-model"},
                        "tools": ["web_search"],
                    },
                    "input_path": "$.request",
                    "output_path": "$.answer",
                },
            }
        ]
        edges = [
            {"source": "input", "target": "research"},
            {"source": "research", "target": "output"},
        ]
        output_path = "$.answer"
    if not valid:
        edges = []
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": str(WORKFLOW_ID),
                "name": "Workflow draft",
                "description": "Editable before publication",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "string"},
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    *middle,
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"output_path": output_path},
                    },
                ],
                "edges": edges,
            },
            "editor": {"positions": {"input": {"x": 0, "y": 0}}},
        }
    )


class RecordingAgentValidator:
    def __init__(self) -> None:
        self.specs: list[Any] = []

    async def validate(self, spec):
        self.specs.append(spec)
        return spec


async def test_draft_can_be_saved_incomplete_but_not_published() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    service = WorkflowService(
        database,
        WorkflowValidator([], ["test"]),
        RecordingAgentValidator(),  # type: ignore[arg-type]
    )
    try:
        saved = await service.save(WORKFLOW_ID, draft(valid=False))
        with pytest.raises(WorkflowValidationError):
            await service.create_version(saved.id)
    finally:
        await database.close()


async def test_structurally_incomplete_agent_node_can_be_saved_but_not_published() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    service = WorkflowService(
        database,
        WorkflowValidator([], ["test"]),
        RecordingAgentValidator(),  # type: ignore[arg-type]
    )
    incomplete = draft()
    incomplete.spec["nodes"].insert(
        1,
        {
            "id": "agent",
            "type": "agent",
            "name": "Agent",
            "config": {
                "agent_version_id": "",
                "input_path": "$.request",
                "output_path": "$.answer",
            },
        },
    )
    try:
        saved = await service.save(WORKFLOW_ID, incomplete)

        assert saved.spec["nodes"][1]["config"]["agent_version_id"] == ""
        with pytest.raises(WorkflowValidationError) as error:
            await service.create_version(saved.id)
        assert "workflow.node.invalid_config" in {issue.code for issue in error.value.issues}
    finally:
        await database.close()


async def test_publishing_validates_inline_agent_and_creates_version() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    agent_validator = RecordingAgentValidator()
    service = WorkflowService(
        database,
        WorkflowValidator(["web_search"], ["test"]),
        agent_validator,  # type: ignore[arg-type]
    )
    try:
        saved = await service.save(WORKFLOW_ID, draft(inline_agent=True))
        version = await service.create_version(saved.id)

        assert version is not None
        assert version.version == 1
        assert agent_validator.specs[0].tools == ["web_search"]
    finally:
        await database.close()


async def test_layout_rejects_unknown_node_ids() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    service = WorkflowService(
        database,
        WorkflowValidator([], ["test"]),
        RecordingAgentValidator(),  # type: ignore[arg-type]
    )
    invalid = draft()
    invalid.editor.positions["missing"] = {"x": 0, "y": 0}  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="unknown nodes"):
            await service.save(WORKFLOW_ID, invalid)
    finally:
        await database.close()
