from uuid import UUID

from heart_of_the_swarm.approval_grants import ApprovalGrant
from heart_of_the_swarm.authorization import AuthorizationRequest
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import ApprovalGrantRepository


class DatabaseApprovalGrantConsumer:
    """Consume durable grants through a short storage transaction per invocation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def consume(
        self,
        grant_id: UUID,
        request: AuthorizationRequest,
        arguments_fingerprint: str,
    ) -> ApprovalGrant:
        async with self.database.session() as session:
            return await ApprovalGrantRepository(session).consume(
                str(grant_id),
                request,
                arguments_fingerprint,
            )
