from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from heart_of_the_swarm import __version__
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.runtime.application import StandaloneAgentRuntime
from heart_of_the_swarm.runtime.models import InvokeRequest, InvokeResponse, RuntimeMetadata


def create_app(
    runtime_factory: Callable[[], StandaloneAgentRuntime] | None = None,
) -> FastAPI:
    factory = runtime_factory or (lambda: StandaloneAgentRuntime(Settings()))

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
        title="Heart of the Swarm Agent Runtime",
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
            raise HTTPException(status_code=502, detail="agent execution failed") from exc

    return app


app = create_app()
