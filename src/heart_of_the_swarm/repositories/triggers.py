from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from heart_of_the_swarm.models import TriggerRecord
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.triggers.models import (
    ScheduleTriggerConfig,
    TriggerDetail,
    TriggerSpec,
)


class TriggerRepository(RepositoryBase):
    """Persist typed trigger definitions without executing them."""

    async def create(self, spec: TriggerSpec) -> TriggerDetail:
        now = datetime.now(UTC)
        next_fire_at = None
        if isinstance(spec.config, ScheduleTriggerConfig):
            next_fire_at = now + timedelta(seconds=spec.config.every_seconds)
        record = TriggerRecord(
            id=str(uuid4()),
            name=spec.name,
            trigger_type=spec.type,
            target_type=spec.target_type,
            target_version_id=str(spec.target_version_id),
            enabled=spec.enabled,
            config=spec.config.model_dump(mode="json"),
            next_fire_at=next_fire_at,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.commit()
        return self._detail(record)

    async def get(self, trigger_id: str) -> TriggerDetail | None:
        record = await self.session.get(TriggerRecord, trigger_id)
        return self._detail(record) if record else None

    async def list(self) -> list[TriggerDetail]:
        records = (
            await self.session.execute(select(TriggerRecord).order_by(TriggerRecord.created_at))
        ).scalars()
        return [self._detail(record) for record in records]

    @staticmethod
    def _detail(record: TriggerRecord) -> TriggerDetail:
        return TriggerDetail(
            id=record.id,
            name=record.name,
            type=record.trigger_type,
            target_type=record.target_type,
            target_version_id=record.target_version_id,
            enabled=record.enabled,
            config=record.config,
            next_fire_at=record.next_fire_at,
            last_fired_at=record.last_fired_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
