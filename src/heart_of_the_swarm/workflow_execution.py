import asyncio
import time
from typing import Any
from uuid import UUID

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.observability import audit_event, audit_exception, trace_context
from heart_of_the_swarm.repositories import WorkflowRepository, WorkflowRunRepository
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflow_agent_versions import DatabaseAgentVersionResolver
from heart_of_the_swarm.workflows import (
    WorkflowExecutionEvent,
    WorkflowGraphFactory,
    WorkflowRuntimePolicy,
    WorkflowValidator,
    WorkflowVersionRunner,
    validate_workflow_input,
)
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
    ) -> None:
        self.database = database
        self.validator = validator
        self.tools = tools

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

    async def emit(self, event: WorkflowExecutionEvent) -> None:
        async with self.database.session() as session:
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
    ) -> None:
        self.settings = settings
        self.database = database
        self.telemetry = telemetry
        self.runner = WorkflowVersionRunner(
            validator,
            tools,
            WorkflowGraphFactory(
                agent_runner=agent_runner,
                agent_versions=DatabaseAgentVersionResolver(database),
                capabilities=tools,
            ),
        )
        self.policy = WorkflowRuntimePolicy(
            timeout_seconds=settings.workflow_timeout_seconds,
            recursion_limit=settings.workflow_recursion_limit,
        )

    async def execute(self, run_id: str, worker_id: str) -> None:
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
                )
                async with self.database.session() as session:
                    await WorkflowRunRepository(session).finish(
                        run_id,
                        result.output,
                        result.executed_nodes,
                    )
                self.telemetry.set_outputs(span, {"output": result.output})
                audit_event(
                    "workflow.execution.completed",
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
