from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from heart_of_the_swarm.models import WorkflowRecord, WorkflowVersionRecord
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.tools import CapabilityContract
from heart_of_the_swarm.workflows.documents import (
    WorkflowDraftDetail,
    WorkflowDraftSave,
    WorkflowSummary,
    WorkflowVersionDetail,
)


class WorkflowRevisionConflict(ValueError):
    """Raised when a stale editor revision attempts to overwrite a workflow."""


class WorkflowRepository(RepositoryBase):
    """Persist mutable workflow drafts and immutable workflow versions."""

    async def save(self, draft: WorkflowDraftSave) -> WorkflowDraftDetail:
        workflow_id = str(draft.spec["id"])
        now = datetime.now(UTC)
        workflow = await self.session.get(WorkflowRecord, workflow_id)
        if workflow is None:
            if draft.expected_revision is not None:
                raise WorkflowRevisionConflict("workflow draft does not exist")
            workflow = WorkflowRecord(
                id=workflow_id,
                name=str(draft.spec.get("name", "Untitled workflow")),
                description=str(draft.spec.get("description", "")),
                spec=draft.spec,
                editor=draft.editor.model_dump(mode="json"),
                revision=1,
                latest_version=0,
                created_at=now,
                updated_at=now,
            )
            self.session.add(workflow)
        else:
            if draft.expected_revision != workflow.revision:
                raise WorkflowRevisionConflict(
                    f"workflow draft revision is {workflow.revision}, "
                    f"not {draft.expected_revision or 'unspecified'}"
                )
            workflow.name = str(draft.spec.get("name", "Untitled workflow"))
            workflow.description = str(draft.spec.get("description", ""))
            workflow.spec = draft.spec
            workflow.editor = draft.editor.model_dump(mode="json")
            workflow.revision += 1
            workflow.updated_at = now
        await self.session.commit()
        return self._detail(workflow)

    async def list_workflows(self) -> list[WorkflowSummary]:
        rows = (
            await self.session.execute(
                select(WorkflowRecord).order_by(WorkflowRecord.updated_at.desc())
            )
        ).scalars()
        return [self._summary(workflow) for workflow in rows]

    async def get(self, workflow_id: str) -> WorkflowDraftDetail | None:
        workflow = await self.session.get(WorkflowRecord, workflow_id)
        return self._detail(workflow) if workflow else None

    async def create_version(
        self,
        workflow_id: str,
        capability_contracts: Sequence[CapabilityContract] = (),
        expected_revision: int | None = None,
    ) -> WorkflowVersionDetail | None:
        statement = select(WorkflowRecord).where(WorkflowRecord.id == workflow_id).with_for_update()
        workflow = (await self.session.execute(statement)).scalar_one_or_none()
        if workflow is None:
            return None
        if expected_revision is not None and workflow.revision != expected_revision:
            raise WorkflowRevisionConflict("workflow draft changed during version creation")
        workflow.latest_version += 1
        version = WorkflowVersionRecord(
            id=str(uuid4()),
            workflow_id=workflow.id,
            version=workflow.latest_version,
            spec=workflow.spec,
            editor=workflow.editor,
            capability_contracts=[
                contract.model_dump(mode="json") for contract in capability_contracts
            ],
            created_at=datetime.now(UTC),
        )
        self.session.add(version)
        await self.session.commit()
        return self._version_detail(version)

    @staticmethod
    def _summary(workflow: WorkflowRecord) -> WorkflowSummary:
        return WorkflowSummary(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            revision=workflow.revision,
            latest_version=workflow.latest_version,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        )

    @classmethod
    def _detail(cls, workflow: WorkflowRecord) -> WorkflowDraftDetail:
        return WorkflowDraftDetail(
            **cls._summary(workflow).model_dump(),
            spec=workflow.spec,
            editor=workflow.editor,
        )

    @staticmethod
    def _version_detail(version: WorkflowVersionRecord) -> WorkflowVersionDetail:
        return WorkflowVersionDetail(
            id=version.id,
            workflow_id=version.workflow_id,
            version=version.version,
            spec=version.spec,
            editor=version.editor,
            capability_contracts=version.capability_contracts,
            created_at=version.created_at,
        )
