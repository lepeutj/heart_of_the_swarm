from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryBase:
    """Shared session access for one persistence responsibility."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _required(self, model: Any, record_id: str) -> Any:
        record = await self.session.get(model, record_id)
        if record is None:
            raise ValueError(f"{model.__tablename__} record not found")
        return record
