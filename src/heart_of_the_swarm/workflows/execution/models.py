from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.workflows.state import WorkflowState


class ExecutionInterruption(BaseModel):
    """Portable description of one technical LangGraph interruption."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interrupt_id: str
    kind: Literal["approval"]
    node_id: str
    prompt: str
    response_schema: dict[str, Any]


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    output: Any | None = None
    state: WorkflowState
    executed_nodes: tuple[str, ...]
    interrupted: bool = False
    checkpoint_id: str | None = None
    interruption: ExecutionInterruption | None = None
    loop_iterations: dict[str, int] = Field(default_factory=dict)
