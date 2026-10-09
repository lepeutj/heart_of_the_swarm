import asyncio

import pytest

from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeRunStatus
from heart_of_the_swarm.runtime.runs import RuntimeRunCapacityError, RuntimeRunManager


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
    ) -> InvokeResponse:
        assert runtime_input == {"request": "value"}
        assert run_id is not None
        assert trace_id is not None
        self.started.set()
        await self.release.wait()
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
        (3, "runtime.completed"),
    ]
    await manager.close()


async def test_remote_run_failure_returns_only_a_safe_error() -> None:
    class FailingRuntime:
        async def invoke(self, *_args, **_kwargs) -> InvokeResponse:
            raise RuntimeError("secret provider detail")

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


async def test_remote_run_store_rejects_unbounded_active_work() -> None:
    runtime = BlockingRuntime()
    manager = RuntimeRunManager(runtime, max_retained_runs=1)

    await manager.start({"request": "value"})
    await runtime.started.wait()

    with pytest.raises(RuntimeRunCapacityError):
        await manager.start({"request": "value"})

    runtime.release.set()
    await manager.close()
