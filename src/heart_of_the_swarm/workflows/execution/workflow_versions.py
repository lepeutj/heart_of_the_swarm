from typing import Protocol
from uuid import UUID

from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot


class WorkflowVersionResolver(Protocol):
    """Resolve immutable child workflows without coupling graph compilation to storage."""

    async def resolve(self, workflow_version_id: UUID) -> WorkflowVersionSnapshot | None: ...
