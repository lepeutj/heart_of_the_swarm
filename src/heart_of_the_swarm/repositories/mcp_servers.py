from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from heart_of_the_swarm.models import MCPServerRecord
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.tools import MCPServerCreate, MCPServerDetail


class MCPServerRepository(RepositoryBase):
    """Persist MCP source definitions shared by API and worker processes."""

    async def list(self) -> list[MCPServerDetail]:
        records = (
            await self.session.execute(select(MCPServerRecord).order_by(MCPServerRecord.name))
        ).scalars()
        return [self._detail(record) for record in records]

    async def get(self, server_id: str) -> MCPServerDetail | None:
        record = await self.session.get(MCPServerRecord, server_id)
        return self._detail(record) if record is not None else None

    async def get_by_name(self, name: str) -> MCPServerDetail | None:
        record = (
            await self.session.execute(select(MCPServerRecord).where(MCPServerRecord.name == name))
        ).scalar_one_or_none()
        return self._detail(record) if record is not None else None

    async def create(self, source: MCPServerCreate) -> MCPServerDetail:
        if await self.get_by_name(source.name) is not None:
            raise ValueError(f"MCP server already exists: {source.name}")
        now = datetime.now(UTC)
        record = MCPServerRecord(
            id=str(uuid4()),
            name=source.name,
            url=str(source.url),
            enabled=source.enabled,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(f"MCP server already exists: {source.name}") from exc
        return self._detail(record)

    async def seed(self, sources: dict[str, str]) -> None:
        for name, url in sources.items():
            if await self.get_by_name(name) is not None:
                continue
            now = datetime.now(UTC)
            self.session.add(
                MCPServerRecord(
                    id=str(uuid4()),
                    name=name,
                    url=url,
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()

    @staticmethod
    def _detail(record: MCPServerRecord) -> MCPServerDetail:
        return MCPServerDetail(
            id=record.id,
            name=record.name,
            url=record.url,
            enabled=record.enabled,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
