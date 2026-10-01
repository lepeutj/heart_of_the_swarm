from uuid import UUID

from pydantic import ValidationError

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository
from heart_of_the_swarm.validator import AgentSpecValidator
from heart_of_the_swarm.workflows import WorkflowSpec, WorkflowValidator
from heart_of_the_swarm.workflows.configs import AgentNodeConfig, InlineAgentSource
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftDetail,
    WorkflowDraftSave,
    WorkflowSummary,
    WorkflowVersionDetail,
)


class WorkflowService:
    """Persist editable workflow documents and publish validated immutable versions."""

    def __init__(
        self,
        database: Database,
        validator: WorkflowValidator,
        agent_validator: AgentSpecValidator,
    ) -> None:
        self.database = database
        self.validator = validator
        self.agent_validator = agent_validator

    async def save(self, workflow_id: UUID, draft: WorkflowDraftSave) -> WorkflowDraftDetail:
        draft_id = self._draft_id(draft.spec)
        if workflow_id != draft_id:
            raise ValueError("workflow URL and specification IDs do not match")
        node_ids = self._draft_node_ids(draft.spec)
        unknown_layout_nodes = set(draft.editor.positions) - node_ids
        if unknown_layout_nodes:
            names = ", ".join(sorted(unknown_layout_nodes))
            raise ValueError(f"editor layout contains unknown nodes: {names}")
        async with self.database.session() as session:
            return await WorkflowRepository(session).save(draft)

    async def list(self) -> list[WorkflowSummary]:
        async with self.database.session() as session:
            return await WorkflowRepository(session).list_workflows()

    async def get(self, workflow_id: UUID) -> WorkflowDraftDetail | None:
        async with self.database.session() as session:
            return await WorkflowRepository(session).get(str(workflow_id))

    async def create_version(self, workflow_id: UUID) -> WorkflowVersionDetail | None:
        async with self.database.session() as session:
            draft = await WorkflowRepository(session).get(str(workflow_id))
        if draft is None:
            return None
        try:
            spec = WorkflowSpec.model_validate(draft.spec)
        except ValidationError as exc:
            raise ValueError("workflow draft is not a valid WorkflowSpec") from exc
        validated = self.validator.validate(spec)
        for node in validated.nodes:
            if isinstance(node.config, AgentNodeConfig) and isinstance(
                node.config.source, InlineAgentSource
            ):
                await self.agent_validator.validate(node.config.source.agent)
        async with self.database.session() as session:
            return await WorkflowRepository(session).create_version(
                str(workflow_id), expected_revision=draft.revision
            )

    @staticmethod
    def _draft_id(spec: dict[str, object]) -> UUID:
        try:
            return UUID(str(spec["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("workflow draft requires a valid id") from exc

    @staticmethod
    def _draft_node_ids(spec: dict[str, object]) -> set[str]:
        nodes = spec.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("workflow draft nodes must be a list")
        return {
            node["id"]
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
