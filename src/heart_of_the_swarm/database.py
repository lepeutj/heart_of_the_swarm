from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from heart_of_the_swarm.models import Base

SCHEMA_REVISION = "0011_workflow_interruptions"
REQUIRED_TABLES = {
    "agents",
    "agent_versions",
    "design_sessions",
    "runs",
    "run_events",
    "trajectory_steps",
    "model_usage",
    "workflows",
    "workflow_versions",
    "mcp_servers",
    "workflow_runs",
    "workflow_run_events",
    "triggers",
    "execution_threads",
    "workflow_interruptions",
}


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite"):
            path = url.rsplit("///", 1)[-1]
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def create_schema(self) -> None:
        """Create tables for isolated tests; deployed databases use Alembic migrations."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def readiness(self) -> str:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
            missing = REQUIRED_TABLES - tables
            if missing:
                raise RuntimeError(f"database tables are missing: {', '.join(sorted(missing))}")
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        if revision != SCHEMA_REVISION:
            raise RuntimeError(
                f"database schema is {revision or 'unversioned'}, expected {SCHEMA_REVISION}"
            )
        return revision

    def session(self) -> AsyncSession:
        return self.sessions()

    async def close(self) -> None:
        await self.engine.dispose()
