import asyncio
import time
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.observability import audit_event, audit_exception, trace_context
from heart_of_the_swarm.repositories import (
    WorkflowInterruptionRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflow_agent_versions import DatabaseAgentVersionResolver
from heart_of_the_swarm.workflow_version_resolver import DatabaseWorkflowVersionResolver
from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    WorkflowExecutionEvent,
    WorkflowGraphFactory,
    WorkflowValidator,
    WorkflowVersionRunner,
    validate_workflow_input,
)
from heart_of_the_swarm.workflows.execution.checkpoints import WorkflowCheckpointProvider
from heart_of_the_swarm.workflows.runs import (
    WorkflowRunAccepted,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowRunOrigin,
)


class WorkflowRunService:
    """Queue and read durable executions of immutable workflow versions."""

    def __init__(
        self,
        database: Database,
        validator: WorkflowValidator,
        tools: ToolRegistry,
        checkpoints: WorkflowCheckpointProvider | None = None,
    ) -> None:
        self.database = database
        self.validator = validator
        self.tools = tools
        self.checkpoints = checkpoints

    async def queue(
        self,
        version_id: UUID,
        workflow_input: dict[str, Any],
        trace_id: str,
        origin: WorkflowRunOrigin | None = None,
    ) -> WorkflowRunAccepted:
        async with self.database.session() as session:
            version = await WorkflowRepository(session).get_version(str(version_id))
        if version is None:
            raise ValueError("workflow version not found")
        workflow = self.validator.validate(version.spec)
        validate_workflow_input(workflow, workflow_input)
        self.tools.verify_contracts(version.capability_contracts)
        async with self.database.session() as session:
            return await WorkflowRunRepository(session).queue(
                version,
                trace_id,
                workflow_input,
                origin,
            )

    async def resume(
        self,
        run_id: UUID,
        checkpoint_id: str,
        trace_id: str,
    ) -> WorkflowRunAccepted:
        saver = self._checkpointer()
        async with self.database.session() as session:
            previous = await WorkflowRunRepository(session).get(str(run_id))
            pending = await WorkflowInterruptionRepository(session).pending_for_run(str(run_id))
        if previous is None:
            raise ValueError("workflow run not found")
        if pending is not None:
            raise ValueError("human approval requires an explicit response")
        if previous.thread_id is None:
            raise ValueError("workflow run does not belong to a resumable thread")
        config = {
            "configurable": {
                "thread_id": str(previous.thread_id),
                "checkpoint_id": checkpoint_id,
            }
        }
        if await saver.aget_tuple(config) is None:
            raise ValueError("checkpoint not found for execution thread")
        latest = await saver.aget_tuple({"configurable": {"thread_id": str(previous.thread_id)}})
        latest_checkpoint_id = (
            latest.config.get("configurable", {}).get("checkpoint_id") if latest else None
        )
        if latest_checkpoint_id != checkpoint_id:
            raise ValueError("only the latest checkpoint can be resumed")
        async with self.database.session() as session:
            return await WorkflowRunRepository(session).resume(
                str(run_id),
                checkpoint_id,
                trace_id,
            )

    def _checkpointer(self) -> BaseCheckpointSaver:
        if self.checkpoints is None or self.checkpoints.saver is None:
            raise RuntimeError("durable workflow checkpointing is not initialized")
        return self.checkpoints.saver

    async def get(self, run_id: UUID) -> WorkflowRunDetail | None:
        async with self.database.session() as session:
            return await WorkflowRunRepository(session).get(str(run_id))

    async def events(self, run_id: UUID) -> list[WorkflowRunEvent] | None:
        async with self.database.session() as session:
            repository = WorkflowRunRepository(session)
            if await repository.get(str(run_id)) is None:
                return None
            return await repository.list_events(str(run_id))


class _DatabaseWorkflowEventSink:
    """Persist portable runner events as workflow business events."""

    def __init__(self, database: Database, run_id: str) -> None:
        self.database = database
        self.run_id = run_id
        self._write_lock = asyncio.Lock()

    async def emit(self, event: WorkflowExecutionEvent) -> None:
        # Parallel nodes may emit concurrently, while the durable sequence counter is per run.
        async with self._write_lock, self.database.session() as session:
            await WorkflowRunRepository(session).add_event(
                self.run_id,
                event.event_type,
                event.data,
            )


class WorkflowExecutor:
    """Own durable run lifecycle around the portable workflow runner."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        validator: WorkflowValidator,
        tools: ToolRegistry,
        agent_runner: AgentRunner,
        telemetry: Telemetry,
        checkpoints: WorkflowCheckpointProvider | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.telemetry = telemetry
        self.checkpoints = checkpoints
        self.runner = WorkflowVersionRunner(
            validator,
            tools,
            WorkflowGraphFactory(
                agent_runner=agent_runner,
                agent_versions=DatabaseAgentVersionResolver(database),
                workflow_versions=DatabaseWorkflowVersionResolver(database),
                validator=validator,
                capabilities=tools,
            ),
        )
        self.policy = ExecutionPolicy(
            timeout_seconds=settings.workflow_timeout_seconds,
            recursion_limit=settings.workflow_recursion_limit,
        )

    async def execute(
        self,
        run_id: str,
        worker_id: str,
        *,
        interrupt_after: tuple[str, ...] = (),
    ) -> None:
        async with self.database.session() as session:
            context = await WorkflowRunRepository(session).get_execution_context(run_id)
        if context is None:
            raise ValueError("workflow run execution context not found")

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, worker_id, stop_heartbeat))
        started = time.perf_counter()

        with (
            trace_context(context.trace_id),
            self.telemetry.span(
                "workflow.run",
                {
                    "app.trace_id": context.trace_id,
                    "workflow.id": str(context.version.workflow_id),
                    "workflow.version": context.version.version,
                    "workflow.version_id": str(context.version.id),
                    "run.id": run_id,
                    "execution.thread_id": context.thread_id,
                    "execution.attempt_index": context.attempt_index,
                },
            ) as span,
        ):
            self.telemetry.set_inputs(
                span,
                {
                    "input": context.input,
                    "workflow_spec": context.version.spec.model_dump(mode="json"),
                },
            )
            audit_event(
                "workflow.execution.started",
                workflow_id=str(context.version.workflow_id),
                workflow_version=context.version.version,
                run_id=run_id,
            )
            try:
                result = await self.runner.run(
                    context.version,
                    context.input,
                    self.policy,
                    _DatabaseWorkflowEventSink(self.database, run_id),
                    execution_id=run_id,
                    thread_id=context.thread_id,
                    checkpoint_id=context.resume_checkpoint_id,
                    resume_value=context.resume_value,
                    checkpointer=self._checkpointer(),
                    interrupt_after=interrupt_after,
                )
                async with self.database.session() as session:
                    repository = WorkflowRunRepository(session)
                    if result.interrupted:
                        if result.checkpoint_id is None:
                            raise RuntimeError("interrupted workflow has no durable checkpoint")
                        if result.interruption is not None:
                            await WorkflowInterruptionRepository(session).create_approval(
                                run_id,
                                result.interruption.node_id,
                                result.checkpoint_id,
                            )
                        else:
                            await repository.interrupt(
                                run_id,
                                result.checkpoint_id,
                                result.executed_nodes,
                            )
                    else:
                        await repository.finish(
                            run_id,
                            result.output,
                            result.executed_nodes,
                        )
                self.telemetry.set_outputs(span, {"output": result.output})
                audit_event(
                    "workflow.execution.interrupted"
                    if result.interrupted
                    else "workflow.execution.completed",
                    run_id=run_id,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    node_count=len(result.executed_nodes),
                )
            except TimeoutError as exc:
                audit_exception("workflow.execution.timed_out", run_id=run_id)
                async with self.database.session() as session:
                    await WorkflowRunRepository(session).fail(run_id, exc, timed_out=True)
            except Exception as exc:
                audit_exception("workflow.execution.failed", run_id=run_id)
                async with self.database.session() as session:
                    await WorkflowRunRepository(session).fail(run_id, exc)
            finally:
                stop_heartbeat.set()
                await heartbeat

    async def _heartbeat(self, run_id: str, worker_id: str, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.worker_heartbeat_seconds)
                return
            except TimeoutError:
                async with self.database.session() as session:
                    active = await WorkflowRunRepository(session).heartbeat(
                        run_id,
                        worker_id,
                        self.settings.worker_lease_seconds,
                    )
                if not active:
                    return

    def _checkpointer(self) -> BaseCheckpointSaver | None:
        return self.checkpoints.saver if self.checkpoints is not None else None
