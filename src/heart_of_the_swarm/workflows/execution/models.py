from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.workflows.state import WorkflowState


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    output: Any
    state: WorkflowState
    executed_nodes: tuple[str, ...]
