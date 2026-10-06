from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver

from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories import WorkflowInterruptionRepository
from heart_of_the_swarm.workflows.execution.checkpoints import WorkflowCheckpointProvider
from heart_of_the_swarm.workflows.interruptions import (
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import WorkflowRunAccepted


class WorkflowApprovalService:
    """Validate one operator response and queue its durable workflow continuation."""

    def __init__(
        self,
        database: Database,
        checkpoints: WorkflowCheckpointProvider,
    ) -> None:
        self.database = database
        self.checkpoints = checkpoints

    async def get(self, interruption_id: UUID) -> WorkflowInterruptionDetail | None:
        """Read one durable interruption without exposing persistence details."""
        async with self.database.session() as session:
            return await WorkflowInterruptionRepository(session).get(str(interruption_id))

    async def list(
        self,
        status: WorkflowInterruptionStatus | None = WorkflowInterruptionStatus.PENDING,
    ) -> list[WorkflowInterruptionDetail]:
        """List interruptions, defaulting to pending operator work."""
        async with self.database.session() as session:
            return await WorkflowInterruptionRepository(session).list(status)

    async def respond(
        self,
        interruption_id: UUID,
        response: dict[str, Any],
        trace_id: str,
    ) -> WorkflowRunAccepted:
        """Resolve one pending interruption only when its exact checkpoint remains current."""
        async with self.database.session() as session:
            interruption = await WorkflowInterruptionRepository(session).get(str(interruption_id))
        if interruption is None:
            raise ValueError("workflow interruption not found")
        if interruption.status != WorkflowInterruptionStatus.PENDING:
            raise ValueError("workflow interruption is not pending")

        saver = self._checkpointer()
        config = {
            "configurable": {
                "thread_id": str(interruption.thread_id),
                "checkpoint_id": interruption.checkpoint_id,
            }
        }
        if await saver.aget_tuple(config) is None:
            raise ValueError("checkpoint not found for execution thread")
        latest = await saver.aget_tuple(
            {"configurable": {"thread_id": str(interruption.thread_id)}}
        )
        latest_checkpoint_id = (
            latest.config.get("configurable", {}).get("checkpoint_id") if latest else None
        )
        if latest_checkpoint_id != interruption.checkpoint_id:
            raise ValueError("only the latest checkpoint can be resumed")

        async with self.database.session() as session:
            return await WorkflowInterruptionRepository(session).resolve_and_resume(
                str(interruption_id),
                response=response,
                trace_id=trace_id,
            )

    def _checkpointer(self) -> BaseCheckpointSaver:
        if self.checkpoints.saver is None:
            raise RuntimeError("durable workflow checkpointing is not initialized")
        return self.checkpoints.saver
