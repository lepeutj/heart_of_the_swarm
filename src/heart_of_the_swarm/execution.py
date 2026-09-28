import asyncio
import time

from langchain_core.messages import AIMessage

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import (
    RuntimeCallbackHandler,
    audit_event,
    audit_exception,
    trace_context,
)
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.repository import ExecutionContext, Repository
from heart_of_the_swarm.spec import AgentRunAccepted, AgentRunDetail, RunEvent, TrajectoryStep
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.validator import AgentSpecValidator


class RunService:
    def __init__(self, database: Database, validator: AgentSpecValidator) -> None:
        self.database = database
        self.validator = validator

    async def queue(self, agent_id: str, agent_input: str, trace_id: str) -> AgentRunAccepted:
        async with self.database.session() as session:
            repository = Repository(session)
            agent = await repository.get_agent(agent_id)
            if agent is None:
                raise ValueError("agent not found")
            self.validator.validate_execution(agent.spec)
            return await repository.queue_run(agent, trace_id, agent_input)

    async def get(self, run_id: str) -> AgentRunDetail | None:
        async with self.database.session() as session:
            return await Repository(session).get_run(run_id)

    async def list_runs(self, agent_id: str) -> list[AgentRunDetail]:
        async with self.database.session() as session:
            return await Repository(session).list_runs(agent_id)

    async def events(self, run_id: str) -> list[RunEvent] | None:
        async with self.database.session() as session:
            repository = Repository(session)
            if await repository.get_run(run_id) is None:
                return None
            return await repository.list_run_events(run_id)

    async def trajectory(self, run_id: str) -> list[TrajectoryStep] | None:
        async with self.database.session() as session:
            repository = Repository(session)
            if await repository.get_run(run_id) is None:
                return None
            return await repository.list_trajectory_steps(run_id)

    async def cancel(self, run_id: str) -> AgentRunDetail | None:
        async with self.database.session() as session:
            return await Repository(session).request_cancel(run_id)


class AgentExecutor:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        providers: ProviderRegistry,
        validator: AgentSpecValidator,
        factory: AgentFactory,
        telemetry: Telemetry,
    ) -> None:
        self.settings = settings
        self.database = database
        self.providers = providers
        self.validator = validator
        self.factory = factory
        self.telemetry = telemetry

    async def execute(self, run_id: str, worker_id: str) -> None:
        async with self.database.session() as session:
            context = await Repository(session).get_execution_context(run_id)
        if context is None:
            raise ValueError("run execution context not found")

        config = context.agent.spec.model
        usage = RuntimeCallbackHandler("agent", config.provider, config.model_id)
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, worker_id, stop_heartbeat))
        started = time.perf_counter()
        observability_persisted = False

        with (
            trace_context(context.trace_id),
            self.telemetry.span(
                "agent.run",
                {
                    "app.trace_id": context.trace_id,
                    "agent.id": context.agent.id,
                    "agent.version": context.agent.version,
                    "run.id": run_id,
                },
            ) as span,
        ):
            self.telemetry.set_inputs(
                span,
                {
                    "input": context.input,
                    "system_prompt": context.agent.system_prompt,
                    "agent_spec": context.agent.spec.model_dump(mode="json"),
                },
            )
            audit_event("agent.execution.started", agent_name=context.agent.name, run_id=run_id)
            try:
                self.validator.validate_execution(context.agent.spec)
                if await self._cancelled(run_id):
                    await self._persist_observability(context, usage)
                    observability_persisted = True
                    await self._mark_cancelled(run_id)
                    return
                model = self.providers.create_model(config)
                graph = self.factory.create(
                    context.agent.spec,
                    model,
                    system_prompt=context.agent.system_prompt,
                )
                result = await graph.ainvoke(
                    {"messages": [{"role": "user", "content": context.input}]},
                    config={"callbacks": [usage]},
                )
                final = next(
                    (
                        message
                        for message in reversed(result.get("messages", []))
                        if isinstance(message, AIMessage)
                    ),
                    None,
                )
                if final is None:
                    raise RuntimeError("agent returned no final answer")
                output = final.content if isinstance(final.content, str) else str(final.content)
                if await self._cancelled(run_id):
                    await self._persist_observability(context, usage)
                    observability_persisted = True
                    await self._mark_cancelled(run_id)
                    return
                await self._persist_observability(context, usage)
                observability_persisted = True
                async with self.database.session() as session:
                    await Repository(session).finish_run(run_id, output)
                self.telemetry.set_outputs(span, {"output": output})
                audit_event(
                    "agent.execution.completed",
                    run_id=run_id,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    output_characters=len(output),
                )
            except Exception as exc:
                audit_exception("agent.execution.failed", run_id=run_id)
                if not observability_persisted:
                    await self._persist_observability(context, usage)
                async with self.database.session() as session:
                    await Repository(session).fail_run(run_id, exc)
            finally:
                stop_heartbeat.set()
                await heartbeat

    async def _persist_observability(
        self, context: ExecutionContext, callback: RuntimeCallbackHandler
    ) -> None:
        async with self.database.session() as session:
            await Repository(session).add_run_observability(
                context.run_id,
                context.attempt,
                context.trace_id,
                callback.events,
                callback.trajectory,
            )

    async def _heartbeat(self, run_id: str, worker_id: str, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.worker_heartbeat_seconds)
                return
            except TimeoutError:
                async with self.database.session() as session:
                    active = await Repository(session).heartbeat(
                        run_id, worker_id, self.settings.worker_lease_seconds
                    )
                if not active:
                    return

    async def _cancelled(self, run_id: str) -> bool:
        async with self.database.session() as session:
            return await Repository(session).cancellation_requested(run_id)

    async def _mark_cancelled(self, run_id: str) -> None:
        async with self.database.session() as session:
            await Repository(session).cancel_run(run_id)
        audit_event("agent.execution.cancelled", run_id=run_id)
