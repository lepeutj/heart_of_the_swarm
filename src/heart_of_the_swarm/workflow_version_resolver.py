from uuid import UUID

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowRepository
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot


class DatabaseWorkflowVersionResolver:
    """Load immutable workflow versions for nested graph compilation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def resolve(self, workflow_version_id: UUID) -> WorkflowVersionSnapshot | None:
        async with self.database.session() as session:
            return await WorkflowRepository(session).get_version(str(workflow_version_id))
