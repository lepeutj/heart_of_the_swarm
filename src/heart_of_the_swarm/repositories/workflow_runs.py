from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from heart_of_the_swarm.models import WorkflowRunEventRecord, WorkflowRunRecord
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.repositories.workflows import WorkflowRepository
from heart_of_the_swarm.spec import RunStatus
from heart_of_the_swarm.workflows.documents import WorkflowVersionDetail
from heart_of_the_swarm.workflows.runs import (
    WorkflowRunAccepted,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowRunOrigin,
)


@dataclass(frozen=True)
class WorkflowExecutionContext:
    run_id: str
    attempt: int
    trace_id: str
    input: dict[str, Any]
    version: WorkflowVersionDetail


class WorkflowRunRepository(RepositoryBase):
    """Persist workflow-run queue state and product lifecycle events."""

    async def queue(
        self,
        version: WorkflowVersionDetail,
        trace_id: str,
        workflow_input: dict[str, Any],
        origin: WorkflowRunOrigin | None = None,
    ) -> WorkflowRunAccepted:
        run = WorkflowRunRecord(
            id=str(uuid4()),
            workflow_version_id=str(version.id),
            trace_id=trace_id,
            trigger_id=str(origin.trigger_id) if origin else None,
            trigger_type=origin.trigger_type if origin else None,
            trigger_event_id=str(origin.trigger_event_id) if origin else None,
            status=RunStatus.QUEUED,
            input=workflow_input,
            event_sequence=0,
            queued_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        self._add_event(
            run,
            "workflow.queued",
            {
                "workflow_id": str(version.workflow_id),
                "workflow_version": version.version,
                "trigger_id": str(origin.trigger_id) if origin else None,
                "trigger_event_id": str(origin.trigger_event_id) if origin else None,
            },
        )
        await self.session.commit()
        return self._accepted(run, version)

    async def claim_next(self, worker_id: str, lease_seconds: int) -> str | None:
        statement = (
            select(WorkflowRunRecord)
            .where(WorkflowRunRecord.status == RunStatus.QUEUED)
            .order_by(WorkflowRunRecord.queued_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        run = (await self.session.execute(statement)).scalar_one_or_none()
        if run is None:
            return None
        now = datetime.now(UTC)
        run.status = RunStatus.RUNNING
        run.attempt += 1
        run.worker_id = worker_id
        run.started_at = now
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self._add_event(
            run,
            "workflow.started",
            {"worker_id": worker_id, "attempt": run.attempt},
        )
        await self.session.commit()
        return run.id

    async def get_execution_context(self, run_id: str) -> WorkflowExecutionContext | None:
        run = await self.session.get(WorkflowRunRecord, run_id)
        if run is None:
            return None
        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        if version is None:
            return None
        return WorkflowExecutionContext(
            run_id=run.id,
            attempt=run.attempt,
            trace_id=run.trace_id,
            input=run.input,
            version=version,
        )

    async def get(self, run_id: str) -> WorkflowRunDetail | None:
        run = await self.session.get(WorkflowRunRecord, run_id)
        if run is None:
            return None
        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        return self._detail(run, version) if version else None

    async def list_events(self, run_id: str) -> list[WorkflowRunEvent]:
        statement = (
            select(WorkflowRunEventRecord)
            .where(WorkflowRunEventRecord.workflow_run_id == run_id)
            .order_by(WorkflowRunEventRecord.sequence)
        )
        events = (await self.session.execute(statement)).scalars()
        return [
            WorkflowRunEvent(
                id=event.id,
                workflow_run_id=event.workflow_run_id,
                sequence=event.sequence,
                event_type=event.event_type,
                data=event.data,
                created_at=event.created_at,
            )
            for event in events
        ]

    async def add_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        run = await self._required(WorkflowRunRecord, run_id)
        self._add_event(run, event_type, data)
        await self.session.commit()

    async def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        run = await self.session.get(WorkflowRunRecord, run_id)
        if run is None or run.worker_id != worker_id or run.status != RunStatus.RUNNING:
            return False
        now = datetime.now(UTC)
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.commit()
        return True

    async def finish(self, run_id: str, output: Any, executed_nodes: tuple[str, ...]) -> None:
        run = await self._required(WorkflowRunRecord, run_id)
        run.status = RunStatus.COMPLETED
        run.output = output
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_event(
            run,
            "workflow.completed",
            {"executed_nodes": list(executed_nodes)},
        )
        await self.session.commit()

    async def fail(self, run_id: str, error: BaseException, *, timed_out: bool = False) -> None:
        run = await self._required(WorkflowRunRecord, run_id)
        run.status = RunStatus.TIMED_OUT if timed_out else RunStatus.FAILED
        run.error = f"{type(error).__name__}: {error}"
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_event(
            run,
            "workflow.timed_out" if timed_out else "workflow.failed",
            {"error_type": type(error).__name__, "error_message": str(error)},
        )
        await self.session.commit()

    async def recover_expired(self) -> int:
        """Fail abandoned runs; replay requires future checkpoint and idempotency contracts."""
        now = datetime.now(UTC)
        statement = select(WorkflowRunRecord).where(
            WorkflowRunRecord.status == RunStatus.RUNNING,
            WorkflowRunRecord.lease_expires_at.is_not(None),
            WorkflowRunRecord.lease_expires_at < now,
        )
        runs = list((await self.session.execute(statement)).scalars())
        for run in runs:
            run.status = RunStatus.FAILED
            run.error = "Worker lease expired; automatic workflow replay is disabled"
            run.completed_at = now
            self._add_event(run, "workflow.failed", {"reason": "worker_lease_expired"})
            run.worker_id = None
            run.heartbeat_at = None
            run.lease_expires_at = None
        if runs:
            await self.session.commit()
        return len(runs)

    def _add_event(
        self,
        run: WorkflowRunRecord,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
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
    def _accepted(
        run: WorkflowRunRecord,
        version: WorkflowVersionDetail,
    ) -> WorkflowRunAccepted:
        return WorkflowRunAccepted(
            run_id=run.id,
            trace_id=run.trace_id,
            workflow_id=version.workflow_id,
            workflow_version_id=version.id,
            workflow_version=version.version,
            status=run.status,
            trigger_id=run.trigger_id,
            trigger_type=run.trigger_type,
            trigger_event_id=run.trigger_event_id,
        )

    @classmethod
    def _detail(
        cls,
        run: WorkflowRunRecord,
        version: WorkflowVersionDetail,
    ) -> WorkflowRunDetail:
        return WorkflowRunDetail(
            **cls._accepted(run, version).model_dump(),
            input=run.input,
            output=run.output,
            error=run.error,
            attempt=run.attempt,
            queued_at=run.queued_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
