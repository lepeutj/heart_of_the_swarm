from uuid import UUID

from pydantic import ValidationError

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import AgentRepository, WorkflowRepository
from heart_of_the_swarm.tools import CapabilityContract, ToolRegistry
from heart_of_the_swarm.validator import AgentSpecValidator
from heart_of_the_swarm.workflows import WorkflowSpec, WorkflowValidator
from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConnectorNodeConfig,
    InlineAgentSource,
    SubworkflowNodeConfig,
    VersionedAgentSource,
)
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftDetail,
    WorkflowDraftSave,
    WorkflowSummary,
    WorkflowVersionDetail,
)
from heart_of_the_swarm.workflows.spec import ValidatedWorkflowSpec
from heart_of_the_swarm.workflows.subworkflows import (
    MAX_SUBWORKFLOW_DEPTH,
    validate_subworkflow_mappings,
)


class WorkflowService:
    """Persist editable workflow documents and publish validated immutable versions."""

    def __init__(
        self,
        database: Database,
        validator: WorkflowValidator,
        agent_validator: AgentSpecValidator,
        tools: ToolRegistry,
    ) -> None:
        self.database = database
        self.validator = validator
        self.agent_validator = agent_validator
        self.tools = tools

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

    async def latest_version(self, workflow_id: UUID) -> WorkflowVersionDetail | None:
        async with self.database.session() as session:
            return await WorkflowRepository(session).get_latest_version(str(workflow_id))

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
        await self._validate_subworkflow_dependencies(
            validated,
            workflow_stack=(workflow_id,),
            version_stack=(),
            depth=0,
        )
        capability_ids: set[str] = set()
        for node in validated.nodes:
            if isinstance(node.config, ConnectorNodeConfig):
                capability_ids.add(node.config.capability_id)
            elif isinstance(node.config, AgentNodeConfig):
                if isinstance(node.config.source, InlineAgentSource):
                    agent_spec = await self.agent_validator.validate(node.config.source.agent)
                else:
                    source = node.config.source
                    if not isinstance(source, VersionedAgentSource):
                        raise TypeError("validated agent node has an invalid source")
                    async with self.database.session() as session:
                        version = await AgentRepository(session).get_version(
                            str(source.agent_version_id)
                        )
                    if version is None:
                        raise ValueError(f"agent version not found: {source.agent_version_id}")
                    agent_spec = version.spec
                capability_ids.update(agent_spec.tools)
        contracts: list[CapabilityContract] = [
            self.tools.contract(capability_id) for capability_id in sorted(capability_ids)
        ]
        async with self.database.session() as session:
            return await WorkflowRepository(session).create_version(
                str(workflow_id), contracts, expected_revision=draft.revision
            )

    async def _validate_subworkflow_dependencies(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        workflow_stack: tuple[UUID, ...],
        version_stack: tuple[UUID, ...],
        depth: int,
    ) -> None:
        """Reject missing, incompatible, recursive, or excessively nested child versions."""
        for node in workflow.nodes:
            if not isinstance(node.config, SubworkflowNodeConfig):
                continue
            version_id = node.config.workflow_version_id
            async with self.database.session() as session:
                child = await WorkflowRepository(session).get_version(str(version_id))
            if child is None:
                raise ValueError(f"subworkflow version not found: {version_id}")
            if child.workflow_id in workflow_stack or version_id in version_stack:
                raise ValueError(f"subworkflow dependency is recursive at node '{node.id}'")
            if depth + 1 > MAX_SUBWORKFLOW_DEPTH:
                raise ValueError(
                    f"subworkflow dependency exceeds depth {MAX_SUBWORKFLOW_DEPTH} at node "
                    f"'{node.id}'"
                )
            child_workflow = self.validator.validate(child.spec)
            try:
                validate_subworkflow_mappings(node.config, child_workflow)
            except ValueError as exc:
                raise ValueError(
                    f"invalid subworkflow mappings at node '{node.id}': {exc}"
                ) from exc
            await self._validate_subworkflow_dependencies(
                child_workflow,
                workflow_stack=(*workflow_stack, child.workflow_id),
                version_stack=(*version_stack, version_id),
                depth=depth + 1,
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
