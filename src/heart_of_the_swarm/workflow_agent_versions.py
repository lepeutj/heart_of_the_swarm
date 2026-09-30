from uuid import UUID

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repository import Repository
from heart_of_the_swarm.workflows.execution.agent_versions import ResolvedAgentVersion


class DatabaseAgentVersionResolver:
    """Load immutable agent versions for workflow execution."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def resolve(self, agent_version_id: UUID) -> ResolvedAgentVersion | None:
        async with self.database.session() as session:
            agent = await Repository(session).get_agent_version(str(agent_version_id))
        if agent is None:
            return None
        return ResolvedAgentVersion(spec=agent.spec, system_prompt=agent.system_prompt)
