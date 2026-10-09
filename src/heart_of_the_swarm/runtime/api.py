import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status

from heart_of_the_swarm import __version__
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.http_policy import HTTPPolicy, RequestBodyLimitMiddleware
from heart_of_the_swarm.runtime.models import (
    InvokeRequest,
    InvokeResponse,
    RuntimeMetadata,
    RuntimeRunAccepted,
    RuntimeRunDetail,
    RuntimeRunEvent,
)
from heart_of_the_swarm.runtime.runs import RuntimeRunCapacityError, RuntimeRunManager


class StandaloneRuntime(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    def metadata(self) -> RuntimeMetadata: ...

    async def invoke(
        self,
        runtime_input: Any,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> InvokeResponse: ...


def create_app(
    runtime_factory: Callable[[], StandaloneRuntime] | None = None,
    *,
    title: str = "Heart of the Swarm Agent Runtime",
    http_policy: HTTPPolicy | None = None,
    service_token: str | None = None,
    max_retained_runs: int | None = None,
) -> FastAPI:
    if runtime_factory is None:
        from heart_of_the_swarm.runtime.application import StandaloneAgentRuntime

        settings = Settings()

        def factory() -> StandaloneRuntime:
            return StandaloneAgentRuntime(settings)

        selected_http_policy = http_policy or settings.http_policy()
        selected_service_token = service_token or (
            settings.runtime_api_token.get_secret_value()
            if settings.runtime_api_token is not None
            else None
        )
        selected_max_retained_runs = (
            max_retained_runs
            if max_retained_runs is not None
            else settings.runtime_max_retained_runs
        )

    else:
        factory = runtime_factory
        selected_http_policy = http_policy or HTTPPolicy()
        selected_service_token = service_token
        selected_max_retained_runs = max_retained_runs if max_retained_runs is not None else 100

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not selected_service_token:
            raise ValueError("standalone runtime requires RUNTIME_API_TOKEN")
        runtime = factory()
        await runtime.initialize()
        app.state.runtime = runtime
        app.state.runs = RuntimeRunManager(runtime, selected_max_retained_runs)
        try:
            yield
        finally:
            await app.state.runs.close()
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
    app.add_middleware(RequestBodyLimitMiddleware, policy=selected_http_policy)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def authenticate_runtime(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not token
            or not hmac.compare_digest(
                token,
                selected_service_token,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    protected = APIRouter(dependencies=[Depends(authenticate_runtime)])

    @protected.get("/metadata", response_model=RuntimeMetadata, response_model_exclude_none=True)
    async def metadata(request: Request) -> RuntimeMetadata:
        return request.app.state.runtime.metadata()

    @protected.post(
        "/invoke",
        response_model=RuntimeRunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def invoke(request: InvokeRequest, http_request: Request) -> RuntimeRunAccepted:
        try:
            return await http_request.app.state.runs.start(request.input)
        except RuntimeRunCapacityError as exc:
            raise HTTPException(
                status_code=429, detail="runtime run capacity is exhausted"
            ) from exc

    @protected.get(
        "/runs/{run_id}", response_model=RuntimeRunDetail, response_model_exclude_none=True
    )
    async def get_run(run_id: str, request: Request) -> RuntimeRunDetail:
        run = await request.app.state.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="runtime run not found")
        return run

    @protected.get("/runs/{run_id}/events", response_model=list[RuntimeRunEvent])
    async def get_run_events(run_id: str, request: Request) -> list[RuntimeRunEvent]:
        events = await request.app.state.runs.events(run_id)
        if events is None:
            raise HTTPException(status_code=404, detail="runtime run not found")
        return events

    app.include_router(protected)

    return app


app = create_app()
