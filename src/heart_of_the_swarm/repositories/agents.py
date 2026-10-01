from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from heart_of_the_swarm.models import AgentRecord, AgentVersionRecord, DesignSessionRecord
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.spec import AgentDetail, AgentSpec, AgentSummary, ModelConfig


class AgentRepository(RepositoryBase):
    """Persist agent definitions, immutable versions, and Builder sessions."""

    async def create(self, spec: AgentSpec, prompt_version: str, system_prompt: str) -> AgentDetail:
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
        return self._detail(agent, version)

    async def list_agents(self) -> list[AgentSummary]:
        rows = (
            await self.session.execute(
                select(AgentRecord, AgentVersionRecord)
                .join(
                    AgentVersionRecord,
                    (AgentVersionRecord.agent_id == AgentRecord.id)
                    & (AgentVersionRecord.version == AgentRecord.active_version),
                )
                .order_by(AgentRecord.created_at.desc())
            )
        ).all()
        return [
            AgentSummary(
                id=agent.id,
                version_id=version.id,
                name=agent.name,
                goal=agent.goal,
                version=agent.active_version,
                created_at=agent.created_at,
            )
            for agent, version in rows
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
        return self._detail(agent, version)

    async def get(self, agent_id: str, version: int | None = None) -> AgentDetail | None:
        agent = await self.session.get(AgentRecord, agent_id)
        if agent is None:
            return None
        version_number = version or agent.active_version
        statement = select(AgentVersionRecord).where(
            AgentVersionRecord.agent_id == agent_id,
            AgentVersionRecord.version == version_number,
        )
        record = (await self.session.execute(statement)).scalar_one_or_none()
        return self._detail(agent, record) if record else None

    async def get_version(self, version_id: str) -> AgentDetail | None:
        statement = (
            select(AgentRecord, AgentVersionRecord)
            .join(AgentVersionRecord, AgentVersionRecord.agent_id == AgentRecord.id)
            .where(AgentVersionRecord.id == version_id)
        )
        row = (await self.session.execute(statement)).one_or_none()
        return self._detail(*row) if row else None

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

    @staticmethod
    def _detail(agent: AgentRecord, version: AgentVersionRecord) -> AgentDetail:
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
