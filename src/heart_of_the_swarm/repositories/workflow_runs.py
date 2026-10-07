from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from heart_of_the_swarm.models import (
    ExecutionThreadRecord,
    WorkflowInterruptionRecord,
    WorkflowRunEventRecord,
    WorkflowRunRecord,
)
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.repositories.workflows import WorkflowRepository
from heart_of_the_swarm.spec import RunStatus
from heart_of_the_swarm.workflows.documents import WorkflowVersionDetail
from heart_of_the_swarm.workflows.interruptions import WorkflowInterruptionStatus
from heart_of_the_swarm.workflows.runs import (
    ExecutionThreadStatus,
    WorkflowRunAccepted,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowRunEventBatch,
    WorkflowRunOrigin,
)


@dataclass(frozen=True)
class WorkflowExecutionContext:
    run_id: str
    attempt: int
    trace_id: str
    input: dict[str, Any]
    version: WorkflowVersionDetail
    thread_id: str | None
    resume_checkpoint_id: str | None
    resume_value: dict[str, Any] | None
    attempt_index: int


class WorkflowRunRepository(RepositoryBase):
    """Persist workflow-run queue state and product lifecycle events."""

    async def queue(
        self,
        version: WorkflowVersionDetail,
        trace_id: str,
        workflow_input: dict[str, Any],
        origin: WorkflowRunOrigin | None = None,
    ) -> WorkflowRunAccepted:
        now = datetime.now(UTC)
        thread = ExecutionThreadRecord(
            id=str(uuid4()),
            workflow_version_id=str(version.id),
            status=ExecutionThreadStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        run = WorkflowRunRecord(
            id=str(uuid4()),
            workflow_version_id=str(version.id),
            thread_id=thread.id,
            attempt_index=1,
            trace_id=trace_id,
            trigger_id=str(origin.trigger_id) if origin else None,
            trigger_type=origin.trigger_type if origin else None,
            trigger_event_id=str(origin.trigger_event_id) if origin else None,
            status=RunStatus.QUEUED,
            input=workflow_input,
            event_sequence=0,
            queued_at=now,
        )
        self.session.add(thread)
        self.session.add(run)
        await self.session.flush()
        self._add_event(
            run,
            "thread.created",
            {"thread_id": thread.id},
        )
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

    async def resume(
        self,
        previous_run_id: str,
        checkpoint_id: str,
        trace_id: str,
    ) -> WorkflowRunAccepted:
        previous = await self._required(WorkflowRunRecord, previous_run_id)
        if previous.thread_id is None:
            raise ValueError("workflow run does not belong to a resumable thread")
        if previous.status not in {RunStatus.INTERRUPTED, RunStatus.FAILED}:
            raise ValueError("only interrupted or failed workflow runs can be resumed")
        latest_attempt = (
            await self.session.execute(
                select(func.max(WorkflowRunRecord.attempt_index)).where(
                    WorkflowRunRecord.thread_id == previous.thread_id
                )
            )
        ).scalar_one()
        if latest_attempt != previous.attempt_index:
            raise ValueError("workflow run is not the latest attempt in its thread")

        thread = await self._required(ExecutionThreadRecord, previous.thread_id)
        if thread.status == ExecutionThreadStatus.CLOSED:
            raise ValueError("execution thread is closed")
        version = await WorkflowRepository(self.session).get_version(previous.workflow_version_id)
        if version is None:
            raise ValueError("workflow version not found")

        now = datetime.now(UTC)
        run = WorkflowRunRecord(
            id=str(uuid4()),
            workflow_version_id=previous.workflow_version_id,
            thread_id=previous.thread_id,
            attempt_index=previous.attempt_index + 1,
            resumed_from_run_id=previous.id,
            resume_checkpoint_id=checkpoint_id,
            trace_id=trace_id,
            status=RunStatus.QUEUED,
            input=previous.input,
            event_sequence=0,
            queued_at=now,
        )
        thread.status = ExecutionThreadStatus.ACTIVE
        thread.updated_at = now
        self.session.add(run)
        await self.session.flush()
        self._add_event(
            run,
            "workflow.resumed",
            {
                "thread_id": thread.id,
                "resumed_from_run_id": previous.id,
                "checkpoint_id": checkpoint_id,
                "attempt_index": run.attempt_index,
            },
        )
        self._add_event(run, "workflow.queued", {"workflow_version": version.version})
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
        resume_value = None
        if run.resumed_from_run_id is not None and run.resume_checkpoint_id is not None:
            interruption = (
                await self.session.execute(
                    select(WorkflowInterruptionRecord).where(
                        WorkflowInterruptionRecord.workflow_run_id == run.resumed_from_run_id,
                        WorkflowInterruptionRecord.checkpoint_id == run.resume_checkpoint_id,
                        WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.RESOLVED,
                    )
                )
            ).scalar_one_or_none()
            if interruption is not None:
                resume_value = interruption.response
        return WorkflowExecutionContext(
            run_id=run.id,
            attempt=run.attempt,
            trace_id=run.trace_id,
            input=run.input,
            version=version,
            thread_id=run.thread_id,
            resume_checkpoint_id=run.resume_checkpoint_id,
            resume_value=resume_value,
            attempt_index=run.attempt_index,
        )

    async def get(self, run_id: str) -> WorkflowRunDetail | None:
        run = await self.session.get(WorkflowRunRecord, run_id)
        if run is None:
            return None
        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        return self._detail(run, version) if version else None

    async def list_events(
        self,
        run_id: str,
        after_sequence: int = 0,
    ) -> list[WorkflowRunEvent]:
        """Return persisted events after a cursor in their durable run order."""
        statement = (
            select(WorkflowRunEventRecord)
            .where(
                WorkflowRunEventRecord.workflow_run_id == run_id,
                WorkflowRunEventRecord.sequence > after_sequence,
            )
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

    async def event_batch(
        self,
        run_id: str,
        after_sequence: int,
    ) -> WorkflowRunEventBatch | None:
        """Read run status and subsequent events without loading the immutable version."""
        run = await self.session.get(WorkflowRunRecord, run_id)
        if run is None:
            return None
        return WorkflowRunEventBatch(
            status=run.status,
            thread_id=run.thread_id,
            events=tuple(await self.list_events(run_id, after_sequence)),
        )

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
        if run.thread_id is not None:
            thread = await self._required(ExecutionThreadRecord, run.thread_id)
            thread.status = ExecutionThreadStatus.CLOSED
            thread.updated_at = run.completed_at
        self._add_event(
            run,
            "workflow.completed",
            {"executed_nodes": list(executed_nodes)},
        )
        await self.session.commit()

    async def interrupt(
        self,
        run_id: str,
        checkpoint_id: str,
        executed_nodes: tuple[str, ...],
    ) -> None:
        run = await self._required(WorkflowRunRecord, run_id)
        now = datetime.now(UTC)
        run.status = RunStatus.INTERRUPTED
        run.resume_checkpoint_id = checkpoint_id
        run.completed_at = now
        run.lease_expires_at = None
        if run.thread_id is not None:
            thread = await self._required(ExecutionThreadRecord, run.thread_id)
            thread.status = ExecutionThreadStatus.INTERRUPTED
            thread.updated_at = now
        self._add_event(
            run,
            "workflow.interrupted",
            {
                "checkpoint_id": checkpoint_id,
                "executed_nodes": list(executed_nodes),
            },
        )
        await self.session.commit()

    async def fail(self, run_id: str, error: BaseException, *, timed_out: bool = False) -> None:
        run = await self._required(WorkflowRunRecord, run_id)
        run.status = RunStatus.TIMED_OUT if timed_out else RunStatus.FAILED
        run.error = f"{type(error).__name__}: {error}"
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        if run.thread_id is not None:
            thread = await self._required(ExecutionThreadRecord, run.thread_id)
            thread.status = ExecutionThreadStatus.INTERRUPTED
            thread.updated_at = run.completed_at
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
            if run.thread_id is not None:
                thread = await self._required(ExecutionThreadRecord, run.thread_id)
                thread.status = ExecutionThreadStatus.INTERRUPTED
                thread.updated_at = now
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
            thread_id=run.thread_id,
            attempt_index=run.attempt_index,
            resumed_from_run_id=run.resumed_from_run_id,
            resume_checkpoint_id=run.resume_checkpoint_id,
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
