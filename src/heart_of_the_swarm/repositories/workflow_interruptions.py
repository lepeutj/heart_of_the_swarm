from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jsonschema.validators import validator_for
from sqlalchemy import select, update

from heart_of_the_swarm.models import (
    ExecutionThreadRecord,
    WorkflowInterruptionRecord,
    WorkflowRunEventRecord,
    WorkflowRunRecord,
)
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.repositories.workflow_runs import WorkflowRunRepository
from heart_of_the_swarm.repositories.workflows import WorkflowRepository
from heart_of_the_swarm.spec import RunStatus
from heart_of_the_swarm.workflows.configs import HumanApprovalNodeConfig
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.interruptions import (
    APPROVAL_RESPONSE_SCHEMA,
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import ExecutionThreadStatus, WorkflowRunAccepted


class WorkflowInterruptionRepository(RepositoryBase):
    """Persist and atomically resolve durable human workflow interruptions."""

    async def create_approval(
        self,
        run_id: str,
        node_id: str,
        checkpoint_id: str,
    ) -> WorkflowInterruptionDetail:
        """Atomically interrupt one running workflow with a durable approval request."""
        run = (
            await self.session.execute(
                select(WorkflowRunRecord).where(WorkflowRunRecord.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            raise ValueError("workflow_runs record not found")
        if run.status != RunStatus.RUNNING or run.thread_id is None:
            raise ValueError("workflow run is not running in a durable thread")

        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        if version is None:
            raise ValueError("workflow version not found")
        node = next((item for item in version.spec.nodes if item.id == node_id), None)
        if node is None or node.type != NodeType.HUMAN_APPROVAL:
            raise ValueError("workflow node is not a human approval")
        config = HumanApprovalNodeConfig.model_validate(node.config)

        existing = (
            await self.session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == run.id,
                    WorkflowInterruptionRecord.node_id == node_id,
                    WorkflowInterruptionRecord.checkpoint_id == checkpoint_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("workflow interruption already exists")

        record = WorkflowInterruptionRecord(
            id=str(uuid4()),
            workflow_version_id=run.workflow_version_id,
            thread_id=run.thread_id,
            workflow_run_id=run.id,
            node_id=node_id,
            kind="approval",
            prompt=config.prompt,
            response_schema=APPROVAL_RESPONSE_SCHEMA,
            checkpoint_id=checkpoint_id,
            status=WorkflowInterruptionStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        now = datetime.now(UTC)
        run.status = RunStatus.INTERRUPTED
        run.resume_checkpoint_id = checkpoint_id
        run.completed_at = now
        run.lease_expires_at = None
        thread = await self._required(ExecutionThreadRecord, run.thread_id)
        thread.status = ExecutionThreadStatus.INTERRUPTED
        thread.updated_at = now
        self.session.add(record)
        self._add_event(
            run,
            "approval.requested",
            {
                "interruption_id": record.id,
                "node_id": node_id,
                "checkpoint_id": checkpoint_id,
            },
        )
        self._add_event(
            run,
            "workflow.interrupted",
            {"checkpoint_id": checkpoint_id, "node_id": node_id},
        )
        await self.session.commit()
        return self._detail(record)

    async def get(self, interruption_id: str) -> WorkflowInterruptionDetail | None:
        record = await self.session.get(WorkflowInterruptionRecord, interruption_id)
        return self._detail(record) if record else None

    async def list(
        self,
        status: WorkflowInterruptionStatus | None = None,
    ) -> list[WorkflowInterruptionDetail]:
        """List human interruptions in newest-first product order."""
        statement = select(WorkflowInterruptionRecord)
        if status is not None:
            statement = statement.where(WorkflowInterruptionRecord.status == status)
        records = (
            await self.session.execute(
                statement.order_by(WorkflowInterruptionRecord.created_at.desc())
            )
        ).scalars()
        return [self._detail(record) for record in records]

    async def pending_for_run(self, run_id: str) -> WorkflowInterruptionDetail | None:
        """Return the pending human interruption that owns a run's next resume."""
        record = (
            await self.session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == run_id,
                    WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
                )
            )
        ).scalar_one_or_none()
        return self._detail(record) if record else None

    async def resolve_and_resume(
        self,
        interruption_id: str,
        *,
        response: dict[str, Any],
        trace_id: str,
    ) -> WorkflowRunAccepted:
        """Atomically resolve one approval and create its queued continuation run."""
        record = await self._required(WorkflowInterruptionRecord, interruption_id)
        if record.status != WorkflowInterruptionStatus.PENDING:
            raise ValueError("workflow interruption is not pending")
        self._validate_response(record.response_schema, response)
        now = datetime.now(UTC)
        statement = (
            update(WorkflowInterruptionRecord)
            .where(
                WorkflowInterruptionRecord.id == interruption_id,
                WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
            )
            .values(
                status=WorkflowInterruptionStatus.RESOLVED,
                response=response,
                resolved_at=now,
            )
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("workflow interruption is not pending")
        previous = await self._required(WorkflowRunRecord, record.workflow_run_id)
        self._add_event(
            previous,
            "approval.resolved",
            {
                "interruption_id": record.id,
                "node_id": record.node_id,
                "approved": response["approved"],
            },
        )
        # The run repository commits only after both resolution and continuation exist.
        return await WorkflowRunRepository(self.session).resume(
            record.workflow_run_id,
            record.checkpoint_id,
            trace_id,
        )

    async def cancel(
        self,
        interruption_id: str,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> WorkflowInterruptionDetail:
        """Atomically cancel one pending interruption without creating a resumed run."""
        record = await self._required(WorkflowInterruptionRecord, interruption_id)
        self._validate_context(record, thread_id, checkpoint_id)
        statement = (
            update(WorkflowInterruptionRecord)
            .where(
                WorkflowInterruptionRecord.id == interruption_id,
                WorkflowInterruptionRecord.thread_id == thread_id,
                WorkflowInterruptionRecord.checkpoint_id == checkpoint_id,
                WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
            )
            .values(
                status=WorkflowInterruptionStatus.CANCELLED,
                resolved_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("workflow interruption is not pending")
        await self.session.commit()
        cancelled = await self._required(WorkflowInterruptionRecord, interruption_id)
        return self._detail(cancelled)

    @staticmethod
    def _validate_context(
        record: WorkflowInterruptionRecord,
        thread_id: str,
        checkpoint_id: str,
    ) -> None:
        if record.thread_id != thread_id or record.checkpoint_id != checkpoint_id:
            raise ValueError("workflow interruption does not match the execution context")
        if record.status != WorkflowInterruptionStatus.PENDING:
            raise ValueError("workflow interruption is not pending")

    @staticmethod
    def _validate_response(schema: dict[str, Any], response: dict[str, Any]) -> None:
        validator = validator_for(schema)(schema)
        errors = sorted(validator.iter_errors(response), key=lambda error: list(error.path))
        if errors:
            raise ValueError(f"workflow interruption response is invalid: {errors[0].message}")

    def _add_event(
        self,
        run: WorkflowRunRecord,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Append an event in the same transaction as the interruption lifecycle change."""
        run.event_sequence += 1
        self.session.add(
            WorkflowRunEventRecord(
                id=str(uuid4()),
                workflow_run_id=run.id,
                sequence=run.event_sequence,
                event_type=event_type,
                data=data,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _detail(record: WorkflowInterruptionRecord) -> WorkflowInterruptionDetail:
        return WorkflowInterruptionDetail(
            id=record.id,
            workflow_version_id=record.workflow_version_id,
            thread_id=record.thread_id,
            workflow_run_id=record.workflow_run_id,
            node_id=record.node_id,
            kind=record.kind,
            prompt=record.prompt,
            response_schema=record.response_schema,
            checkpoint_id=record.checkpoint_id,
            status=record.status,
            response=record.response,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
        )
