from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from heart_of_the_swarm.runtime.models import (
    RuntimeMetadata,
    RuntimeRunAccepted,
    RuntimeRunDetail,
    RuntimeRunEvent,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class RemoteRuntimeError(RuntimeError):
    """Expose a stable, credential-free failure from a remote runtime request."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class RemoteRuntimeClient:
    """Call one authenticated standalone runtime without knowing its execution engine."""

    def __init__(
        self,
        endpoint: str,
        service_token: str,
        *,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("remote runtime endpoint must be an HTTP(S) base URL")
        if not service_token:
            raise ValueError("remote runtime service token is required")
        if timeout_seconds <= 0:
            raise ValueError("remote runtime timeout must be positive")

        self._client = httpx.AsyncClient(
            base_url=f"{endpoint.rstrip('/')}/",
            headers={"Authorization": f"Bearer {service_token}"},
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "RemoteRuntimeClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def metadata(self) -> RuntimeMetadata:
        response = await self._request("GET", "metadata", expected_status=200)
        return self._model(response, RuntimeMetadata)

    async def invoke(self, runtime_input: Any) -> RuntimeRunAccepted:
        response = await self._request(
            "POST",
            "invoke",
            expected_status=202,
            json={"input": runtime_input},
        )
        return self._model(response, RuntimeRunAccepted)

    async def get_run(self, run_id: str) -> RuntimeRunDetail:
        response = await self._request("GET", f"runs/{run_id}", expected_status=200)
        return self._model(response, RuntimeRunDetail)

    async def get_events(self, run_id: str) -> list[RuntimeRunEvent]:
        response = await self._request("GET", f"runs/{run_id}/events", expected_status=200)
        try:
            return TypeAdapter(list[RuntimeRunEvent]).validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise self._invalid_response(response) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
        json: Any = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise RemoteRuntimeError(
                "remote_runtime.unavailable",
                "remote runtime is unavailable",
            ) from exc

        if response.status_code == 401:
            raise RemoteRuntimeError(
                "remote_runtime.authentication_failed",
                "remote runtime authentication failed",
                status_code=401,
            )
        if response.status_code != expected_status:
            raise RemoteRuntimeError(
                "remote_runtime.request_failed",
                "remote runtime request failed",
                status_code=response.status_code,
            )
        return response

    @classmethod
    def _model(cls, response: httpx.Response, model: type[ResponseModel]) -> ResponseModel:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise cls._invalid_response(response) from exc

    @staticmethod
    def _invalid_response(response: httpx.Response) -> RemoteRuntimeError:
        return RemoteRuntimeError(
            "remote_runtime.invalid_response",
            "remote runtime returned an invalid response",
            status_code=response.status_code,
        )
