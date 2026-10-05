from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import case, func, select

from heart_of_the_swarm.models import ModelUsageRecord, TrajectoryStepRecord
from heart_of_the_swarm.observability import ModelUsageEvent, TrajectoryEvent
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.spec import TrajectoryStep, UsageSummary


class ObservabilityRepository(RepositoryBase):
    """Persist temporary technical trajectories and model-usage aggregates."""

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

    async def list_trajectory(self, run_id: str) -> list[TrajectoryStep]:
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
