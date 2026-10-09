from datetime import UTC, datetime

import httpx
import pytest

from heart_of_the_swarm.runtime.client import RemoteRuntimeClient, RemoteRuntimeError
from heart_of_the_swarm.runtime.models import RuntimeRunStatus

RUNTIME_TOKEN = "runtime-client-token"  # noqa: S105 - isolated test credential


def run_payload(status: str, **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": "run-1",
        "trace_id": "trace-1",
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload.update(updates)
    return payload


async def test_remote_runtime_client_calls_the_authenticated_contract() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/runtime/metadata":
            return httpx.Response(
                200,
                json={
                    "manifest": {
                        "format_version": "1",
                        "runtime_version": "test",
                        "agent_id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
                        "agent_version_id": "dc36aa94-3399-45d9-b285-e82787e8ca5d",
                        "agent_version": 1,
                        "provider": "openai",
                        "tools": [],
                        "agent_sha256": "0" * 64,
                    },
                    "agent": {"name": "Remote agent"},
                    "observability": {"mlflow": False, "langchain_autolog": False},
                    "runtime_id": "remote-test",
                },
            )
        if request.url.path == "/runtime/invoke":
            return httpx.Response(
                202,
                json={"run_id": "run-1", "trace_id": "trace-1", "status": "queued"},
            )
        if request.url.path == "/runtime/runs/run-1/events":
            return httpx.Response(
                200,
                json=[
                    {
                        "event_id": "event-1",
                        "run_id": "run-1",
                        "trace_id": "trace-1",
                        "sequence": 1,
                        "event_type": "runtime.queued",
                        "created_at": datetime.now(UTC).isoformat(),
                        "data": {},
                    }
                ],
            )
        if request.url.path == "/runtime/runs/run-1":
            return httpx.Response(
                200,
                json=run_payload(
                    "completed",
                    finished_at=datetime.now(UTC).isoformat(),
                    result={"answer": "done"},
                    mlflow_trace_id="mlflow-1",
                ),
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with RemoteRuntimeClient(
        "https://runtime.example/runtime",
        RUNTIME_TOKEN,
        transport=httpx.MockTransport(handle),
    ) as client:
        metadata = await client.metadata()
        accepted = await client.invoke({"request": "hello"})
        run = await client.get_run(accepted.run_id)
        events = await client.get_events(accepted.run_id)

    assert metadata.runtime_id == "remote-test"
    assert accepted.status == RuntimeRunStatus.QUEUED
    assert run.status == RuntimeRunStatus.COMPLETED
    assert run.result == {"answer": "done"}
    assert run.mlflow_trace_id == "mlflow-1"
    assert events[0].event_type == "runtime.queued"
    assert all(
        request.headers["Authorization"] == f"Bearer {RUNTIME_TOKEN}" for request in requests
    )
    assert requests[1].read() == b'{"input":{"request":"hello"}}'


@pytest.mark.parametrize("status", ["queued", "running", "failed", "timed_out"])
async def test_remote_runtime_client_preserves_non_completed_run_states(status: str) -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        error = "runtime execution failed" if status in {"failed", "timed_out"} else None
        return httpx.Response(200, json=run_payload(status, error=error))

    async with RemoteRuntimeClient(
        "https://runtime.example",
        RUNTIME_TOKEN,
        transport=httpx.MockTransport(handle),
    ) as client:
        run = await client.get_run("run-1")

    assert run.status == RuntimeRunStatus(status)


async def test_remote_runtime_client_reports_authentication_failure_safely() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "do not expose remote detail"})

    async with RemoteRuntimeClient(
        "https://runtime.example",
        "wrong-token",  # noqa: S106 - isolated test credential
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(RemoteRuntimeError) as captured:
            await client.metadata()

    assert captured.value.code == "remote_runtime.authentication_failed"
    assert captured.value.status_code == 401
    assert "remote detail" not in str(captured.value)


async def test_remote_runtime_client_normalizes_network_failures() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sensitive network detail", request=request)

    async with RemoteRuntimeClient(
        "https://runtime.example",
        RUNTIME_TOKEN,
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(RemoteRuntimeError) as captured:
            await client.metadata()

    assert captured.value.code == "remote_runtime.unavailable"
    assert captured.value.status_code is None
    assert "sensitive" not in str(captured.value)


async def test_remote_runtime_client_rejects_invalid_runtime_payloads() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with RemoteRuntimeClient(
        "https://runtime.example",
        RUNTIME_TOKEN,
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(RemoteRuntimeError) as captured:
            await client.metadata()

    assert captured.value.code == "remote_runtime.invalid_response"
