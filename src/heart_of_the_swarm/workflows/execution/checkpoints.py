from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Protocol

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class WorkflowCheckpointProvider(Protocol):
    """Expose a configured checkpointer without coupling consumers to its storage backend."""

    saver: BaseCheckpointSaver | None


class DurableWorkflowCheckpoints:
    """Own the lifecycle of the native LangGraph checkpointer used by the application."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.saver: BaseCheckpointSaver | None = None
        self._sqlite_connection: aiosqlite.Connection | None = None
        self._postgres_context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None

    async def start(self) -> BaseCheckpointSaver:
        if self.saver is not None:
            return self.saver

        serializer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=None,
        )
        if self.database_url.startswith("sqlite"):
            path = self.database_url.rsplit("///", 1)[-1]
            self._sqlite_connection = await aiosqlite.connect(path)
            saver = AsyncSqliteSaver(self._sqlite_connection, serde=serializer)
        elif self.database_url.startswith(("postgresql", "postgres")):
            connection_url = self.database_url.replace("+asyncpg", "", 1)
            context = AsyncPostgresSaver.from_conn_string(connection_url, serde=serializer)
            self._postgres_context = context
            saver = await context.__aenter__()
        else:
            raise ValueError("workflow checkpointing supports only SQLite and PostgreSQL")

        await saver.setup()
        self.saver = saver
        return saver

    async def close(self) -> None:
        self.saver = None
        if self._postgres_context is not None:
            await self._postgres_context.__aexit__(None, None, None)
            self._postgres_context = None
        if self._sqlite_connection is not None:
            await self._sqlite_connection.close()
            self._sqlite_connection = None

    async def __aenter__(self) -> BaseCheckpointSaver:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
