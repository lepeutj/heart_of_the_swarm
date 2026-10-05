from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from heart_of_the_swarm.models import RunEventRecord, RunRecord
from heart_of_the_swarm.repositories.agents import AgentRepository
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.spec import (
    AgentDetail,
    AgentRunAccepted,
    AgentRunDetail,
    RunEvent,
    RunStatus,
)


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    attempt: int
    trace_id: str
    input: str
    agent: AgentDetail


class RunRepository(RepositoryBase):
    """Persist queued-run lifecycle, leases, cancellation, and business events."""

    async def queue(self, agent: AgentDetail, trace_id: str, agent_input: str) -> AgentRunAccepted:
        run = RunRecord(
            id=str(uuid4()),
            agent_id=agent.id,
            agent_version=agent.version,
            trace_id=trace_id,
            status=RunStatus.QUEUED,
            input=agent_input,
            queued_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        self._add_event(run.id, RunStatus.QUEUED.value, {"agent_version": agent.version})
        await self.session.commit()
        return self._accepted(run)

    async def claim_next(self, worker_id: str, lease_seconds: int) -> str | None:
        statement = (
            select(RunRecord)
            .where(RunRecord.status == RunStatus.QUEUED)
            .order_by(RunRecord.queued_at)
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
        self._add_event(run.id, "claimed", {"worker_id": worker_id, "attempt": run.attempt})
        await self.session.commit()
        return run.id

    async def get_execution_context(self, run_id: str) -> ExecutionContext | None:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            return None
        agent = await AgentRepository(self.session).get(run.agent_id, run.agent_version)
        if agent is None:
            return None
        return ExecutionContext(
            run_id=run.id,
            attempt=run.attempt,
            trace_id=run.trace_id,
            input=run.input,
            agent=agent,
        )

    async def get(self, run_id: str) -> AgentRunDetail | None:
        run = await self.session.get(RunRecord, run_id)
        return self._detail(run) if run else None

    async def list_runs(self, agent_id: str, limit: int = 50) -> list[AgentRunDetail]:
        statement = (
            select(RunRecord)
            .where(RunRecord.agent_id == agent_id)
            .order_by(RunRecord.queued_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).scalars()
        return [self._detail(run) for run in rows]

    async def list_events(self, run_id: str) -> list[RunEvent]:
        statement = (
            select(RunEventRecord)
            .where(RunEventRecord.run_id == run_id)
            .order_by(RunEventRecord.created_at)
        )
        events = (await self.session.execute(statement)).scalars()
        return [
            RunEvent(
                id=event.id,
                run_id=event.run_id,
                event_type=event.event_type,
                data=event.data,
                created_at=event.created_at,
            )
            for event in events
        ]

    async def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        run = await self.session.get(RunRecord, run_id)
        if run is None or run.worker_id != worker_id or run.status != RunStatus.RUNNING:
            return False
        now = datetime.now(UTC)
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.commit()
        return True

    async def cancellation_requested(self, run_id: str) -> bool:
        run = await self._required(RunRecord, run_id)
        return run.status == RunStatus.CANCEL_REQUESTED

    async def request_cancel(self, run_id: str) -> AgentRunDetail | None:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            return None
        now = datetime.now(UTC)
        if run.status == RunStatus.QUEUED:
            run.status = RunStatus.CANCELLED
            run.cancel_requested_at = now
            run.completed_at = now
            self._add_event(run.id, RunStatus.CANCELLED.value, {"phase": "queued"})
        elif run.status == RunStatus.RUNNING:
            run.status = RunStatus.CANCEL_REQUESTED
            run.cancel_requested_at = now
            self._add_event(run.id, RunStatus.CANCEL_REQUESTED.value, {})
        await self.session.commit()
        return self._detail(run)

    async def finish(self, run_id: str, output: str) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.COMPLETED
        run.output = output
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_event(run.id, RunStatus.COMPLETED.value, {"output_characters": len(output)})
        await self.session.commit()

    async def cancel(self, run_id: str) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_event(run.id, RunStatus.CANCELLED.value, {"phase": "running"})
        await self.session.commit()

    async def fail(self, run_id: str, error: BaseException) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.FAILED
        run.error = f"{type(error).__name__}: {error}"
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_event(
            run.id,
            RunStatus.FAILED.value,
            {"error_type": type(error).__name__, "error_message": str(error)},
        )
        await self.session.commit()

    async def recover_expired(self, max_attempts: int) -> int:
        now = datetime.now(UTC)
        statement = select(RunRecord).where(
            RunRecord.status.in_((RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED)),
            RunRecord.lease_expires_at.is_not(None),
            RunRecord.lease_expires_at < now,
        )
        runs = list((await self.session.execute(statement)).scalars())
        for run in runs:
            if run.status == RunStatus.CANCEL_REQUESTED:
                run.status = RunStatus.CANCELLED
                run.completed_at = now
                self._add_event(
                    run.id,
                    RunStatus.CANCELLED.value,
                    {"reason": "worker_lease_expired"},
                )
            elif run.attempt >= max_attempts:
                run.status = RunStatus.FAILED
                run.error = "Worker lease expired and retry limit was reached"
                run.completed_at = now
                self._add_event(run.id, RunStatus.FAILED.value, {"reason": "retry_limit"})
            else:
                run.status = RunStatus.QUEUED
                run.queued_at = now
                self._add_event(run.id, "requeued", {"reason": "worker_lease_expired"})
            run.worker_id = None
            run.heartbeat_at = None
            run.lease_expires_at = None
        if runs:
            await self.session.commit()
        return len(runs)

    def _add_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        self.session.add(
            RunEventRecord(
                id=str(uuid4()),
                run_id=run_id,
                event_type=event_type,
                data=data,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _accepted(run: RunRecord) -> AgentRunAccepted:
        return AgentRunAccepted(
            run_id=run.id,
            trace_id=run.trace_id,
            agent_id=run.agent_id,
            agent_version=run.agent_version,
            status=run.status,
        )

    @classmethod
    def _detail(cls, run: RunRecord) -> AgentRunDetail:
        return AgentRunDetail(
            **cls._accepted(run).model_dump(),
            input=run.input,
            output=run.output,
            error=run.error,
            attempt=run.attempt,
            queued_at=run.queued_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
