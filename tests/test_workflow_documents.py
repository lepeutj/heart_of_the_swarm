from typing import Any
from uuid import UUID

import pytest

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.workflow_service import WorkflowService
from heart_of_the_swarm.workflows import WorkflowValidationError, WorkflowValidator
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave

WORKFLOW_ID = UUID("47d174a8-b35e-4563-bd86-3bc6b5b5947f")
CHILD_WORKFLOW_ID = UUID("57d174a8-b35e-4563-bd86-3bc6b5b5947f")


def draft(*, inline_agent: bool = False, valid: bool = True) -> WorkflowDraftSave:
    middle = []
    edges = [{"source": "input", "target": "output"}]
    output_path = "$.request"
    if inline_agent:
        middle = [
            {
                "id": "research",
                "type": "agent",
                "name": "Research",
                "config": {
                    "source": {
                        "type": "inline",
                        "agent": {
                            "name": "ResearchAgent",
                            "goal": "Research",
                            "instructions": "Use available tools.",
                            "model": {"provider": "test", "model_id": "test-model"},
                            "tools": ["web_search"],
                        },
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


def composable_draft(
    workflow_id: UUID,
    *,
    child_version_id: UUID | None = None,
    expected_revision: int | None = None,
    include_required_input: bool = True,
) -> WorkflowDraftSave:
    middle: list[dict[str, Any]] = [
        {
            "id": "copy",
            "type": "transform",
            "name": "Copy",
            "config": {"assign": {"$.result": {"from_state": "$.request"}}},
        }
    ]
    if child_version_id is not None:
        middle = [
            {
                "id": "child",
                "type": "subworkflow",
                "name": "Child",
                "config": {
                    "workflow_version_id": str(child_version_id),
                    "inputs": (
                        {"request": {"from_state": "$.request"}} if include_required_input else {}
                    ),
                    "outputs": {"result": {"to_state": "$.result"}},
                },
            }
        ]
    middle_id = middle[0]["id"]
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "2" if child_version_id else "1",
                "id": str(workflow_id),
                "name": f"Workflow {workflow_id}",
                "description": "Composable workflow",
                "input_schema": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                    "additionalProperties": False,
                },
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    *middle,
                    {
                        "id": "output",
                        "type": "output",
                        "name": "Output",
                        "config": {"outputs": {"result": {"from_state": "$.result"}}},
                    },
                ],
                "edges": [
                    {"source": "input", "target": middle_id},
                    {"source": middle_id, "target": "output"},
                ],
            },
            "editor": {},
            "expected_revision": expected_revision,
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
        create_default_registry(),
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
        create_default_registry(),
    )
    incomplete = draft()
    incomplete.spec["nodes"].insert(
        1,
        {
            "id": "agent",
            "type": "agent",
            "name": "Agent",
            "config": {
                "source": {"type": "version", "agent_version_id": ""},
                "input_path": "$.request",
                "output_path": "$.answer",
            },
        },
    )
    try:
        saved = await service.save(WORKFLOW_ID, incomplete)

        assert saved.spec["nodes"][1]["config"]["source"]["agent_version_id"] == ""
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
        create_default_registry(),
    )
    try:
        saved = await service.save(WORKFLOW_ID, draft(inline_agent=True))
        version = await service.create_version(saved.id)

        assert version is not None
        assert version.version == 1
        assert agent_validator.specs[0].tools == ["web_search"]
        assert [contract.capability_id for contract in version.capability_contracts] == [
            "web_search"
        ]
        assert version.capability_contracts[0].source == "builtin"
        assert len(version.capability_contracts[0].schema_fingerprint) == 64
    finally:
        await database.close()


async def test_layout_rejects_unknown_node_ids() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    service = WorkflowService(
        database,
        WorkflowValidator([], ["test"]),
        RecordingAgentValidator(),  # type: ignore[arg-type]
        create_default_registry(),
    )
    invalid = draft()
    invalid.editor.positions["missing"] = {"x": 0, "y": 0}  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="unknown nodes"):
            await service.save(WORKFLOW_ID, invalid)
    finally:
        await database.close()


async def test_publishing_validates_subworkflow_mappings_and_indirect_recursion() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    service = WorkflowService(
        database,
        WorkflowValidator([], ["test"]),
        RecordingAgentValidator(),  # type: ignore[arg-type]
        create_default_registry(),
    )
    try:
        child_saved = await service.save(
            CHILD_WORKFLOW_ID,
            composable_draft(CHILD_WORKFLOW_ID),
        )
        child_version = await service.create_version(child_saved.id)
        assert child_version is not None

        invalid_id = UUID("67d174a8-b35e-4563-bd86-3bc6b5b5947f")
        invalid_saved = await service.save(
            invalid_id,
            composable_draft(
                invalid_id,
                child_version_id=child_version.id,
                include_required_input=False,
            ),
        )
        with pytest.raises(ValueError, match="missing required fields: request"):
            await service.create_version(invalid_saved.id)
        assert await service.latest_version(invalid_id) is None

        parent_saved = await service.save(WORKFLOW_ID, composable_draft(WORKFLOW_ID))
        parent_v1 = await service.create_version(parent_saved.id)
        assert parent_v1 is not None

        child_saved = await service.save(
            CHILD_WORKFLOW_ID,
            composable_draft(
                CHILD_WORKFLOW_ID,
                child_version_id=parent_v1.id,
                expected_revision=child_saved.revision,
            ),
        )
        child_v2 = await service.create_version(child_saved.id)
        assert child_v2 is not None

        parent_saved = await service.save(
            WORKFLOW_ID,
            composable_draft(
                WORKFLOW_ID,
                child_version_id=child_v2.id,
                expected_revision=parent_saved.revision,
            ),
        )
        with pytest.raises(ValueError, match="dependency is recursive"):
            await service.create_version(parent_saved.id)
        assert (await service.latest_version(WORKFLOW_ID)).id == parent_v1.id  # type: ignore[union-attr]
    finally:
        await database.close()
