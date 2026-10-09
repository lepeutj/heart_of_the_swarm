import asyncio

import pytest

from heart_of_the_swarm.runtime.events import RuntimeEventSink
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeRunStatus
from heart_of_the_swarm.runtime.runs import RuntimeRunBusyError, RuntimeRunManager


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(
        self,
        runtime_input: object,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
    ) -> InvokeResponse:
        assert runtime_input == {"request": "value"}
        assert run_id is not None
        assert trace_id is not None
        self.started.set()
        if event_sink is not None:
            await event_sink.emit("agent.started", {"component": "test"})
        await self.release.wait()
        if event_sink is not None:
            await event_sink.emit("agent.completed", {"component": "test"})
        return InvokeResponse(
            run_id=run_id,
            trace_id=trace_id,
            mlflow_trace_id="mlflow-1",
            output={"answer": "done"},
        )


async def test_remote_run_starts_non_blocking_and_exposes_ordered_lifecycle() -> None:
    runtime = BlockingRuntime()
    manager = RuntimeRunManager(runtime)

    accepted = await manager.start({"request": "value"})
    assert accepted.status == RuntimeRunStatus.QUEUED
    await runtime.started.wait()

    running = await manager.get(accepted.run_id)
    assert running is not None
    assert running.status == RuntimeRunStatus.RUNNING
    runtime.release.set()

    for _ in range(100):
        completed = await manager.get(accepted.run_id)
        if completed is not None and completed.status == RuntimeRunStatus.COMPLETED:
            break
        await asyncio.sleep(0)

    assert completed is not None
    assert completed.result == {"answer": "done"}
    assert completed.mlflow_trace_id == "mlflow-1"
    events = await manager.events(accepted.run_id)
    assert events is not None
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "runtime.queued"),
        (2, "runtime.started"),
        (3, "agent.started"),
        (4, "agent.completed"),
        (5, "runtime.completed"),
    ]
    assert all(event.run_id == accepted.run_id for event in events)
    assert all(event.trace_id == accepted.trace_id for event in events)
    assert len({event.event_id for event in events}) == len(events)
    assert events[2].data == {"component": "test"}
    await manager.close()


async def test_remote_run_failure_returns_safe_error_and_logs_context(monkeypatch) -> None:
    class FailingRuntime:
        async def invoke(self, *_args, **_kwargs) -> InvokeResponse:
            raise RuntimeError("secret provider detail")

    audit_records = []
    monkeypatch.setattr(
        "heart_of_the_swarm.runtime.runs.audit_event",
        lambda event, **fields: audit_records.append((event, fields)),
    )
    manager = RuntimeRunManager(FailingRuntime())
    accepted = await manager.start("input")

    for _ in range(100):
        failed = await manager.get(accepted.run_id)
        if failed is not None and failed.status == RuntimeRunStatus.FAILED:
            break
        await asyncio.sleep(0)

    assert failed is not None
    assert failed.error == "runtime execution failed"
    assert "secret" not in failed.model_dump_json()
    assert audit_records == [
        (
            "runtime.execution.failed",
            {
                "level": 40,
                "run_id": accepted.run_id,
                "trace_id": accepted.trace_id,
                "error_type": "RuntimeError",
            },
        )
    ]
    await manager.close()


async def test_remote_run_timeout_has_a_distinct_terminal_status() -> None:
    class TimedOutRuntime:
        async def invoke(self, *_args, **_kwargs) -> InvokeResponse:
            raise TimeoutError

    manager = RuntimeRunManager(TimedOutRuntime())
    accepted = await manager.start("input")

    for _ in range(100):
        timed_out = await manager.get(accepted.run_id)
        if timed_out is not None and timed_out.status == RuntimeRunStatus.TIMED_OUT:
            break
        await asyncio.sleep(0)

    assert timed_out is not None
    assert timed_out.error == "runtime execution timed out"
    await manager.close()


async def test_remote_runtime_rejects_a_second_active_run() -> None:
    runtime = BlockingRuntime()
    manager = RuntimeRunManager(runtime, max_retained_runs=1)

    await manager.start({"request": "value"})
    await runtime.started.wait()

    with pytest.raises(RuntimeRunBusyError):
        await manager.start({"request": "value"})

    runtime.release.set()
    await manager.close()
