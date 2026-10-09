import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from heart_of_the_swarm.observability import audit_event
from heart_of_the_swarm.runtime.events import RuntimeEventSink
from heart_of_the_swarm.runtime.models import (
    InvokeResponse,
    RuntimeRunAccepted,
    RuntimeRunDetail,
    RuntimeRunEvent,
    RuntimeRunStatus,
)


class RuntimeInvoker(Protocol):
    async def invoke(
        self,
        runtime_input: Any,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
    ) -> InvokeResponse: ...


class RuntimeRunBusyError(RuntimeError):
    """Signal that this single-run runtime is already executing work."""


class _RunEventSink:
    def __init__(self, manager: "RuntimeRunManager", run_id: str, trace_id: str) -> None:
        self.manager = manager
        self.run_id = run_id
        self.trace_id = trace_id

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        await self.manager._append_event(  # noqa: SLF001 - bound adapter owns this manager
            self.run_id,
            self.trace_id,
            event_type,
            data or {},
        )


class RuntimeRunManager:
    """Own ephemeral asynchronous run state around one standalone runtime."""

    def __init__(self, runtime: RuntimeInvoker, max_retained_runs: int = 100) -> None:
        if max_retained_runs < 1:
            raise ValueError("max_retained_runs must be positive")
        self.runtime = runtime
        self.max_retained_runs = max_retained_runs
        self._runs: dict[str, RuntimeRunDetail] = {}
        self._events: dict[str, list[RuntimeRunEvent]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self, runtime_input: Any) -> RuntimeRunAccepted:
        """Create one local run and return before its runtime invocation completes."""
        run_id = str(uuid4())
        trace_id = str(uuid4())
        created_at = datetime.now(UTC)
        run = RuntimeRunDetail(
            run_id=run_id,
            trace_id=trace_id,
            status=RuntimeRunStatus.QUEUED,
            created_at=created_at,
        )
        async with self._lock:
            if any(not task.done() for task in self._tasks.values()):
                raise RuntimeRunBusyError("runtime already has an active run")
            self._prune_terminal_runs()
            self._runs[run_id] = run
            self._events[run_id] = [
                RuntimeRunEvent(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=1,
                    event_type="runtime.queued",
                    created_at=created_at,
                )
            ]
            self._tasks[run_id] = asyncio.create_task(
                self._execute(run_id, trace_id, runtime_input),
                name=f"runtime-run-{run_id}",
            )
        return RuntimeRunAccepted(run_id=run_id, trace_id=trace_id, status=run.status)

    async def get(self, run_id: str) -> RuntimeRunDetail | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run is not None else None

    async def events(self, run_id: str) -> list[RuntimeRunEvent] | None:
        async with self._lock:
            events = self._events.get(run_id)
            return list(events) if events is not None else None

    async def close(self) -> None:
        """Cancel active in-memory runs before the runtime process shuts down."""
        async with self._lock:
            tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, run_id: str, trace_id: str, runtime_input: Any) -> None:
        started_at = datetime.now(UTC)
        await self._transition(
            run_id,
            status=RuntimeRunStatus.RUNNING,
            event_type="runtime.started",
            started_at=started_at,
        )
        try:
            response = await self.runtime.invoke(
                runtime_input,
                run_id=run_id,
                trace_id=trace_id,
                event_sink=_RunEventSink(self, run_id, trace_id),
            )
        except TimeoutError:
            audit_event(
                "runtime.execution.timed_out",
                level=logging.ERROR,
                run_id=run_id,
                trace_id=trace_id,
            )
            await self._transition(
                run_id,
                status=RuntimeRunStatus.TIMED_OUT,
                event_type="runtime.timed_out",
                finished_at=datetime.now(UTC),
                error="runtime execution timed out",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keep provider details out of logs while retaining a searchable failure type.
            audit_event(
                "runtime.execution.failed",
                level=logging.ERROR,
                run_id=run_id,
                trace_id=trace_id,
                error_type=type(exc).__name__,
            )
            await self._transition(
                run_id,
                status=RuntimeRunStatus.FAILED,
                event_type="runtime.failed",
                finished_at=datetime.now(UTC),
                error="runtime execution failed",
            )
        else:
            await self._transition(
                run_id,
                status=RuntimeRunStatus.COMPLETED,
                event_type="runtime.completed",
                finished_at=datetime.now(UTC),
                result=response.output,
                mlflow_trace_id=response.mlflow_trace_id,
            )
        finally:
            async with self._lock:
                self._tasks.pop(run_id, None)

    async def _transition(
        self,
        run_id: str,
        *,
        status: RuntimeRunStatus,
        event_type: str,
        **updates: Any,
    ) -> None:
        async with self._lock:
            current = self._runs[run_id]
            self._runs[run_id] = current.model_copy(update={"status": status, **updates})
            events = self._events[run_id]
            events.append(
                RuntimeRunEvent(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    trace_id=current.trace_id,
                    sequence=len(events) + 1,
                    event_type=event_type,
                    created_at=datetime.now(UTC),
                )
            )

    async def _append_event(
        self,
        run_id: str,
        trace_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Append one invocation event in the order observed by this runtime."""
        async with self._lock:
            events = self._events[run_id]
            events.append(
                RuntimeRunEvent(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=len(events) + 1,
                    event_type=event_type,
                    created_at=datetime.now(UTC),
                    data=data,
                )
            )

    def _prune_terminal_runs(self) -> None:
        """Remove oldest terminal runs until one bounded-store slot is available."""
        terminal = {
            RuntimeRunStatus.COMPLETED,
            RuntimeRunStatus.FAILED,
            RuntimeRunStatus.TIMED_OUT,
        }
        candidates = sorted(
            (run for run in self._runs.values() if run.status in terminal),
            key=lambda run: run.created_at,
        )
        while len(self._runs) >= self.max_retained_runs and candidates:
            expired = candidates.pop(0)
            self._runs.pop(expired.run_id, None)
            self._events.pop(expired.run_id, None)
            self._tasks.pop(expired.run_id, None)
