import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, Field

from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.execution.execution_budget import (
    ExecutionBudget,
    InMemoryExecutionBudget,
    ModelCallBudgetCallback,
)
from heart_of_the_swarm.workflows.execution.graph_factory import WorkflowGraphFactory
from heart_of_the_swarm.workflows.execution.models import ExecutionResult
from heart_of_the_swarm.workflows.execution.state_budget import (
    WorkflowStateBudgetError,
    enforce_state_size,
)
from heart_of_the_swarm.workflows.execution.validation import validate_workflow_input
from heart_of_the_swarm.workflows.spec import ValidatedWorkflowNode
from heart_of_the_swarm.workflows.subworkflows import MAX_SUBWORKFLOW_DEPTH
from heart_of_the_swarm.workflows.validation import WorkflowValidator


class ExecutionPolicy(BaseModel):
    """Portable execution bounds applied to one workflow invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: float = Field(gt=0, le=3_600)
    recursion_limit: int = Field(ge=2, le=10_000)
    max_subworkflow_depth: int = Field(default=MAX_SUBWORKFLOW_DEPTH, ge=1, le=32)
    max_handoffs: int = Field(default=20, ge=1, le=100)
    max_state_bytes: int = Field(default=5_000_000, ge=1_024, le=100_000_000)
    max_model_calls: int = Field(default=100, ge=1, le=10_000)
    max_tool_calls: int = Field(default=100, ge=1, le=10_000)


class WorkflowExecutionEvent(BaseModel):
    """Runtime event envelope independent from its eventual destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str
    data: dict[str, Any]


class WorkflowEventSink(Protocol):
    async def emit(self, event: WorkflowExecutionEvent) -> None:
        """Consume one runtime event without controlling workflow execution."""


class WorkflowVersionRunner:
    """Execute an already-loaded immutable workflow version without persistence."""

    def __init__(
        self,
        validator: WorkflowValidator,
        capabilities: ToolRegistry,
        graphs: WorkflowGraphFactory,
    ) -> None:
        self.validator = validator
        self.capabilities = capabilities
        self.graphs = graphs

    async def run(
        self,
        version: WorkflowVersionSnapshot,
        input_data: dict[str, Any],
        policy: ExecutionPolicy,
        event_sink: WorkflowEventSink | None = None,
        *,
        execution_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
        resume_value: dict[str, Any] | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        interrupt_after: tuple[str, ...] = (),
        execution_budget: ExecutionBudget | None = None,
        agent_callbacks: Sequence[BaseCallbackHandler] = (),
    ) -> ExecutionResult:
        """Validate, build, and invoke one LangGraph workflow version."""
        self.capabilities.verify_contracts(version.capability_contracts)
        workflow = self.validator.validate(version.spec)
        validate_workflow_input(workflow, input_data)
        try:
            enforce_state_size(input_data, policy.max_state_bytes)
        except WorkflowStateBudgetError as exc:
            raise WorkflowExecutionError(
                WorkflowExecutionIssue(
                    code=exc.code,
                    message=str(exc),
                    workflow_id=workflow.id,
                )
            ) from exc
        budget = execution_budget or InMemoryExecutionBudget(
            policy.max_model_calls,
            policy.max_tool_calls,
        )
        graph = await self.graphs.acreate(
            workflow,
            workflow_run_id=execution_id,
            event_sink=self._event_adapter(event_sink),
            checkpointer=checkpointer,
            workflow_version_id=str(version.id),
            max_subworkflow_depth=policy.max_subworkflow_depth,
            max_handoffs=policy.max_handoffs,
            max_state_bytes=policy.max_state_bytes,
            interrupt_after=tuple(path for path in interrupt_after if "/" in path),
            agent_callbacks=(ModelCallBudgetCallback(budget), *agent_callbacks),
            execution_budget=budget,
        )

        async with asyncio.timeout(policy.timeout_seconds):
            result = await graph.ainvoke(
                None if checkpoint_id else input_data,
                recursion_limit=policy.recursion_limit,
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                resume_value=resume_value,
                interrupt_after=tuple(path for path in interrupt_after if "/" not in path),
            )
        if result.checkpoint_id and event_sink is not None:
            await event_sink.emit(
                WorkflowExecutionEvent(
                    event_type="checkpoint.created",
                    data={
                        "checkpoint_id": result.checkpoint_id,
                        "thread_id": thread_id,
                    },
                )
            )
        return result

    @staticmethod
    def _event_adapter(
        sink: WorkflowEventSink | None,
    ) -> Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]] | None:
        if sink is None:
            return None

        async def emit(
            event_type: str,
            _node: ValidatedWorkflowNode,
            data: dict[str, Any],
        ) -> None:
            await sink.emit(WorkflowExecutionEvent(event_type=event_type, data=data))

        return emit
