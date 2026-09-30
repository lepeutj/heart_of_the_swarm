from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from heart_of_the_swarm.models import (
    AgentRecord,
    AgentVersionRecord,
    DesignSessionRecord,
    ModelUsageRecord,
    RunEventRecord,
    RunRecord,
    TrajectoryStepRecord,
)
from heart_of_the_swarm.observability import ModelUsageEvent, TrajectoryEvent
from heart_of_the_swarm.spec import (
    AgentDetail,
    AgentRunAccepted,
    AgentRunDetail,
    AgentSpec,
    AgentSummary,
    ModelConfig,
    RunEvent,
    RunStatus,
    TrajectoryStep,
    UsageSummary,
)


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    attempt: int
    trace_id: str
    input: str
    agent: AgentDetail


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_agent(
        self, spec: AgentSpec, prompt_version: str, system_prompt: str
    ) -> AgentDetail:
        now = datetime.now(UTC)
        agent = AgentRecord(
            id=str(uuid4()), name=spec.name, goal=spec.goal, active_version=1, created_at=now
        )
        version = AgentVersionRecord(
            id=str(uuid4()),
            agent_id=agent.id,
            version=1,
            spec=spec.model_dump(mode="json"),
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            created_at=now,
        )
        self.session.add_all([agent, version])
        await self.session.commit()
        return self._agent_detail(agent, version)

    async def list_agents(self) -> list[AgentSummary]:
        result = await self.session.execute(
            select(AgentRecord).order_by(AgentRecord.created_at.desc())
        )
        return [
            AgentSummary(
                id=agent.id,
                name=agent.name,
                goal=agent.goal,
                version=agent.active_version,
                created_at=agent.created_at,
            )
            for agent in result.scalars()
        ]

    async def add_version(
        self, agent_id: str, spec: AgentSpec, prompt_version: str, system_prompt: str
    ) -> AgentDetail | None:
        statement = select(AgentRecord).where(AgentRecord.id == agent_id).with_for_update()
        agent = (await self.session.execute(statement)).scalar_one_or_none()
        if agent is None:
            return None
        agent.active_version += 1
        agent.name = spec.name
        agent.goal = spec.goal
        version = AgentVersionRecord(
            id=str(uuid4()),
            agent_id=agent.id,
            version=agent.active_version,
            spec=spec.model_dump(mode="json"),
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            created_at=datetime.now(UTC),
        )
        self.session.add(version)
        await self.session.commit()
        return self._agent_detail(agent, version)

    async def get_agent(self, agent_id: str, version: int | None = None) -> AgentDetail | None:
        agent = await self.session.get(AgentRecord, agent_id)
        if agent is None:
            return None
        version_number = version or agent.active_version
        statement = select(AgentVersionRecord).where(
            AgentVersionRecord.agent_id == agent_id,
            AgentVersionRecord.version == version_number,
        )
        record = (await self.session.execute(statement)).scalar_one_or_none()
        return self._agent_detail(agent, record) if record else None

    async def get_agent_version(self, version_id: str) -> AgentDetail | None:
        statement = (
            select(AgentRecord, AgentVersionRecord)
            .join(AgentVersionRecord, AgentVersionRecord.agent_id == AgentRecord.id)
            .where(AgentVersionRecord.id == version_id)
        )
        row = (await self.session.execute(statement)).one_or_none()
        return self._agent_detail(*row) if row else None

    async def start_design(
        self,
        trace_id: str,
        task: str,
        builder: ModelConfig,
        builder_prompt: str,
    ) -> str:
        design = DesignSessionRecord(
            id=str(uuid4()),
            trace_id=trace_id,
            task=task,
            builder_provider=builder.provider,
            builder_model_id=builder.model_id,
            builder_prompt=builder_prompt,
            status="running",
            created_at=datetime.now(UTC),
        )
        self.session.add(design)
        await self.session.commit()
        return design.id

    async def complete_design(self, design_id: str, spec: AgentSpec) -> None:
        design = await self._required(DesignSessionRecord, design_id)
        design.status = "completed"
        design.generated_spec = spec.model_dump(mode="json")
        design.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def fail_design(
        self, design_id: str, error: BaseException, generated_spec: AgentSpec | None = None
    ) -> None:
        design = await self._required(DesignSessionRecord, design_id)
        design.status = "failed"
        design.error = f"{type(error).__name__}: {error}"
        if generated_spec is not None:
            design.generated_spec = generated_spec.model_dump(mode="json")
        design.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def queue_run(
        self, agent: AgentDetail, trace_id: str, agent_input: str
    ) -> AgentRunAccepted:
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
        self._add_run_event(run.id, RunStatus.QUEUED.value, {"agent_version": agent.version})
        await self.session.commit()
        return self._run_accepted(run)

    async def claim_next_run(self, worker_id: str, lease_seconds: int) -> str | None:
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
        self._add_run_event(run.id, "claimed", {"worker_id": worker_id, "attempt": run.attempt})
        await self.session.commit()
        return run.id

    async def get_execution_context(self, run_id: str) -> ExecutionContext | None:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            return None
        agent = await self.get_agent(run.agent_id, run.agent_version)
        if agent is None:
            return None
        return ExecutionContext(
            run_id=run.id,
            attempt=run.attempt,
            trace_id=run.trace_id,
            input=run.input,
            agent=agent,
        )

    async def get_run(self, run_id: str) -> AgentRunDetail | None:
        run = await self.session.get(RunRecord, run_id)
        return self._run_detail(run) if run else None

    async def list_runs(self, agent_id: str, limit: int = 50) -> list[AgentRunDetail]:
        statement = (
            select(RunRecord)
            .where(RunRecord.agent_id == agent_id)
            .order_by(RunRecord.queued_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).scalars()
        return [self._run_detail(run) for run in rows]

    async def list_run_events(self, run_id: str) -> list[RunEvent]:
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

    async def add_trajectory_steps(
        self, run_id: str, attempt: int, events: list[TrajectoryEvent]
    ) -> None:
        self.session.add_all(self._trajectory_records(run_id, attempt, events))
        await self.session.commit()

    async def list_trajectory_steps(self, run_id: str) -> list[TrajectoryStep]:
        statement = (
            select(TrajectoryStepRecord)
            .where(TrajectoryStepRecord.run_id == run_id)
            .order_by(TrajectoryStepRecord.attempt, TrajectoryStepRecord.sequence)
        )
        steps = (await self.session.execute(statement)).scalars()
        return [
            TrajectoryStep(
                id=step.id,
                run_id=step.run_id,
                attempt=step.attempt,
                sequence=step.sequence,
                event_type=step.event_type,
                component=step.component,
                langchain_run_id=step.langchain_run_id,
                parent_run_id=step.parent_run_id,
                payload=step.payload,
                created_at=step.created_at,
            )
            for step in steps
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
            self._add_run_event(run.id, RunStatus.CANCELLED.value, {"phase": "queued"})
        elif run.status == RunStatus.RUNNING:
            run.status = RunStatus.CANCEL_REQUESTED
            run.cancel_requested_at = now
            self._add_run_event(run.id, RunStatus.CANCEL_REQUESTED.value, {})
        await self.session.commit()
        return self._run_detail(run)

    async def finish_run(self, run_id: str, output: str) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.COMPLETED
        run.output = output
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_run_event(run.id, RunStatus.COMPLETED.value, {"output_characters": len(output)})
        await self.session.commit()

    async def cancel_run(self, run_id: str) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_run_event(run.id, RunStatus.CANCELLED.value, {"phase": "running"})
        await self.session.commit()

    async def fail_run(self, run_id: str, error: BaseException) -> None:
        run = await self._required(RunRecord, run_id)
        run.status = RunStatus.FAILED
        run.error = f"{type(error).__name__}: {error}"
        run.completed_at = datetime.now(UTC)
        run.lease_expires_at = None
        self._add_run_event(
            run.id,
            RunStatus.FAILED.value,
            {"error_type": type(error).__name__, "error_message": str(error)},
        )
        await self.session.commit()

    async def recover_expired_runs(self, max_attempts: int) -> int:
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
                self._add_run_event(
                    run.id,
                    RunStatus.CANCELLED.value,
                    {"reason": "worker_lease_expired"},
                )
            elif run.attempt >= max_attempts:
                run.status = RunStatus.FAILED
                run.error = "Worker lease expired and retry limit was reached"
                run.completed_at = now
                self._add_run_event(run.id, RunStatus.FAILED.value, {"reason": "retry_limit"})
            else:
                run.status = RunStatus.QUEUED
                run.queued_at = now
                self._add_run_event(run.id, "requeued", {"reason": "worker_lease_expired"})
            run.worker_id = None
            run.heartbeat_at = None
            run.lease_expires_at = None
        if runs:
            await self.session.commit()
        return len(runs)

    async def add_usage(
        self,
        events: list[ModelUsageEvent],
        trace_id: str,
        run_id: str | None = None,
    ) -> None:
        self.session.add_all(self._usage_records(events, trace_id, run_id))
        await self.session.commit()

    async def add_run_observability(
        self,
        run_id: str,
        attempt: int,
        trace_id: str,
        usage: list[ModelUsageEvent],
        trajectory: list[TrajectoryEvent],
    ) -> None:
        self.session.add_all(self._usage_records(usage, trace_id, run_id))
        self.session.add_all(self._trajectory_records(run_id, attempt, trajectory))
        await self.session.commit()

    async def usage_summary(self) -> list[UsageSummary]:
        statement = (
            select(
                ModelUsageRecord.provider,
                ModelUsageRecord.model_id,
                ModelUsageRecord.stage,
                func.count(ModelUsageRecord.id),
                func.sum(case((ModelUsageRecord.success.is_(False), 1), else_=0)),
                func.sum(ModelUsageRecord.input_tokens),
                func.sum(ModelUsageRecord.output_tokens),
                func.sum(ModelUsageRecord.total_tokens),
                func.sum(ModelUsageRecord.cost),
                func.avg(ModelUsageRecord.latency_ms),
            )
            .group_by(ModelUsageRecord.provider, ModelUsageRecord.model_id, ModelUsageRecord.stage)
            .order_by(ModelUsageRecord.provider, ModelUsageRecord.model_id)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            UsageSummary(
                provider=row[0],
                model_id=row[1],
                stage=row[2],
                calls=row[3] or 0,
                failures=row[4] or 0,
                input_tokens=row[5] or 0,
                output_tokens=row[6] or 0,
                total_tokens=row[7] or 0,
                cost=row[8] or 0,
                average_latency_ms=row[9] or 0,
            )
            for row in rows
        ]

    async def _required(self, model, record_id: str):
        record = await self.session.get(model, record_id)
        if record is None:
            raise ValueError(f"{model.__tablename__} record not found")
        return record

    def _add_run_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
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
    def _usage_records(
        events: list[ModelUsageEvent], trace_id: str, run_id: str | None
    ) -> list[ModelUsageRecord]:
        now = datetime.now(UTC)
        return [
            ModelUsageRecord(
                id=str(uuid4()),
                run_id=run_id,
                trace_id=trace_id,
                stage=event.stage,
                provider=event.provider,
                model_id=event.model_id,
                resolved_provider=event.resolved_provider,
                resolved_model_id=event.resolved_model_id,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                total_tokens=event.total_tokens,
                cost=event.cost,
                latency_ms=event.latency_ms,
                success=event.success,
                error_type=event.error_type,
                created_at=now,
            )
            for event in events
        ]

    @staticmethod
    def _trajectory_records(
        run_id: str, attempt: int, events: list[TrajectoryEvent]
    ) -> list[TrajectoryStepRecord]:
        return [
            TrajectoryStepRecord(
                id=str(uuid4()),
                run_id=run_id,
                attempt=attempt,
                sequence=event.sequence,
                event_type=event.event_type,
                component=event.component,
                langchain_run_id=event.langchain_run_id,
                parent_run_id=event.parent_run_id,
                payload=event.payload,
                created_at=event.created_at,
            )
            for event in events
        ]

    @staticmethod
    def _agent_detail(agent: AgentRecord, version: AgentVersionRecord) -> AgentDetail:
        return AgentDetail(
            id=agent.id,
            version_id=version.id,
            name=agent.name,
            goal=agent.goal,
            version=version.version,
            created_at=agent.created_at,
            spec=AgentSpec.model_validate(version.spec),
            prompt_version=version.prompt_version,
            system_prompt=version.system_prompt,
        )

    @staticmethod
    def _run_accepted(run: RunRecord) -> AgentRunAccepted:
        return AgentRunAccepted(
            run_id=run.id,
            trace_id=run.trace_id,
            agent_id=run.agent_id,
            agent_version=run.agent_version,
            status=run.status,
        )

    @classmethod
    def _run_detail(cls, run: RunRecord) -> AgentRunDetail:
        return AgentRunDetail(
            **cls._run_accepted(run).model_dump(),
            input=run.input,
            output=run.output,
            error=run.error,
            attempt=run.attempt,
            queued_at=run.queued_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
