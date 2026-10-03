from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request

from heart_of_the_swarm import __version__
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.runtime.models import InvokeRequest, InvokeResponse, RuntimeMetadata


class StandaloneRuntime(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    def metadata(self) -> RuntimeMetadata: ...

    async def invoke(self, runtime_input: Any) -> InvokeResponse: ...


def create_app(
    runtime_factory: Callable[[], StandaloneRuntime] | None = None,
    *,
    title: str = "Heart of the Swarm Agent Runtime",
) -> FastAPI:
    if runtime_factory is None:
        from heart_of_the_swarm.runtime.application import StandaloneAgentRuntime

        def factory() -> StandaloneRuntime:
            return StandaloneAgentRuntime(Settings())

    else:
        factory = runtime_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = factory()
        await runtime.initialize()
        app.state.runtime = runtime
        try:
            yield
        finally:
            close = getattr(runtime, "close", None)
            if close is not None:
                await close()

    app = FastAPI(
        title=title,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metadata", response_model=RuntimeMetadata)
    async def metadata(request: Request) -> RuntimeMetadata:
        return request.app.state.runtime.metadata()

    @app.post("/invoke", response_model=InvokeResponse)
    async def invoke(request: InvokeRequest, http_request: Request) -> InvokeResponse:
        try:
            return await http_request.app.state.runtime.invoke(request.input)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="runtime execution failed") from exc

    return app


app = create_app()
