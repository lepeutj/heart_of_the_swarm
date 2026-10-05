from typing import Any
from uuid import UUID, uuid4

from heart_of_the_swarm.triggers.errors import (
    TriggerDisabledError,
    TriggerInvocationError,
    TriggerNotFoundError,
)
from heart_of_the_swarm.triggers.models import TriggerTargetType, TriggerType
from heart_of_the_swarm.triggers.service import TriggerService
from heart_of_the_swarm.workflow_execution import WorkflowRunService
from heart_of_the_swarm.workflows.runs import WorkflowRunAccepted, WorkflowRunOrigin


class WebhookTriggerService:
    """Translate one external JSON event into a durable workflow run."""

    def __init__(
        self,
        triggers: TriggerService,
        workflow_runs: WorkflowRunService,
    ) -> None:
        self.triggers = triggers
        self.workflow_runs = workflow_runs

    async def invoke(
        self,
        trigger_id: UUID,
        payload: dict[str, Any],
        trace_id: str,
    ) -> WorkflowRunAccepted:
        trigger = await self.triggers.get(trigger_id)
        if trigger is None:
            raise TriggerNotFoundError("trigger not found")
        if not trigger.enabled:
            raise TriggerDisabledError("trigger is disabled")
        if trigger.type != TriggerType.WEBHOOK:
            raise TriggerInvocationError("trigger is not a webhook")
        if trigger.target_type != TriggerTargetType.WORKFLOW:
            raise TriggerInvocationError("webhook agent targets are not supported yet")

        return await self.workflow_runs.queue(
            trigger.target_version_id,
            payload,
            trace_id,
            WorkflowRunOrigin(
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                trigger_event_id=uuid4(),
            ),
        )
