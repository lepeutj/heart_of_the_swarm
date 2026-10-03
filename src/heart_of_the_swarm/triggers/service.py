from uuid import UUID

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories.agents import AgentRepository
from heart_of_the_swarm.repositories.triggers import TriggerRepository
from heart_of_the_swarm.repositories.workflows import WorkflowRepository
from heart_of_the_swarm.triggers.models import (
    TriggerDetail,
    TriggerSpec,
    TriggerTargetType,
)


class TriggerService:
    """Validate immutable targets and persist trigger definitions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(self, spec: TriggerSpec) -> TriggerDetail:
        async with self.database.session() as session:
            if spec.target_type == TriggerTargetType.WORKFLOW:
                target = await WorkflowRepository(session).get_version(str(spec.target_version_id))
            else:
                target = await AgentRepository(session).get_version(str(spec.target_version_id))
            if target is None:
                raise ValueError(f"{spec.target_type} version not found")
            return await TriggerRepository(session).create(spec)

    async def get(self, trigger_id: UUID) -> TriggerDetail | None:
        async with self.database.session() as session:
            return await TriggerRepository(session).get(str(trigger_id))

    async def list(self) -> list[TriggerDetail]:
        async with self.database.session() as session:
            return await TriggerRepository(session).list()
