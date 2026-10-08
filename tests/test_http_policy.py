from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from heart_of_the_swarm.api import app as control_plane_app
from heart_of_the_swarm.http_policy import HTTPPolicy, RequestBodyLimitMiddleware
from heart_of_the_swarm.runtime.api import create_app
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeMetadata


def limited_app(calls: list[bytes]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        policy=HTTPPolicy(max_request_bytes=16),
    )

    @app.post("/record")
    async def record(request: Request) -> dict[str, int]:
        body = await request.body()
        calls.append(body)
        return {"size": len(body)}

    return app


def test_declared_oversized_body_is_rejected_before_handler() -> None:
    calls: list[bytes] = []
    with TestClient(limited_app(calls)) as client:
        response = client.post(
            "/record",
            content=b"small",
            headers={"content-length": "17"},
        )

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "http.request_too_large",
            "message": "request body exceeds configured limit",
            "max_request_bytes": 16,
        }
    }
    assert calls == []


def test_oversized_stream_without_content_length_is_rejected_before_handler() -> None:
    calls: list[bytes] = []
    with TestClient(limited_app(calls)) as client:
        response = client.post(
            "/record",
            content=(chunk for chunk in (b"12345678", b"901234567")),
        )

    assert response.status_code == 413
    assert calls == []


def test_false_content_length_does_not_bypass_streamed_size_check() -> None:
    calls: list[bytes] = []
    with TestClient(limited_app(calls)) as client:
        response = client.post(
            "/record",
            content=b"12345678901234567",
            headers={"content-length": "1"},
        )

    assert response.status_code == 413
    assert calls == []


def test_body_at_limit_is_replayed_to_handler() -> None:
    calls: list[bytes] = []
    body = b"1234567890123456"
    with TestClient(limited_app(calls)) as client:
        response = client.post("/record", content=body)

    assert response.status_code == 200
    assert response.json() == {"size": 16}
    assert calls == [body]


def test_standalone_runtime_rejects_before_invocation() -> None:
    class FakeRuntime:
        calls = 0

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

        def metadata(self) -> RuntimeMetadata:
            raise AssertionError("metadata is not used")

        async def invoke(self, runtime_input: object) -> InvokeResponse:
            self.calls += 1
            return InvokeResponse(run_id="run", output=runtime_input)

    runtime = FakeRuntime()
    app = create_app(
        lambda: runtime,
        http_policy=HTTPPolicy(max_request_bytes=16),
    )
    with TestClient(app) as client:
        response = client.post("/invoke", content=b'{"input":"too large"}')

    assert response.status_code == 413
    assert runtime.calls == 0


async def test_control_plane_rejects_before_routing() -> None:
    transport = ASGITransport(app=control_plane_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/skills",
            content=b"x" * 2_000_001,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "http.request_too_large"
