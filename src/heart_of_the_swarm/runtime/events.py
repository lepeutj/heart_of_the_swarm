import asyncio
from concurrent.futures import Future
from typing import Any, Protocol
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


class RuntimeEventSink(Protocol):
    """Receive one safe product event from a standalone runtime invocation."""

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


class RuntimeEventCallbackHandler(BaseCallbackHandler):
    """Forward safe model and tool lifecycle events without exposing arguments or content."""

    def __init__(self, sink: RuntimeEventSink) -> None:
        self.sink = sink
        self._loop = asyncio.get_running_loop()
        self._components: dict[UUID, str] = {}
        self._pending: list[Future[None]] = []

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        _messages: list[list[Any]],
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        component = str(serialized.get("name") or serialized.get("id", ["unknown"])[-1])
        self._components[run_id] = component
        self._schedule("model.started", component)

    def on_llm_end(self, _response: Any, *, run_id: UUID, **_kwargs: Any) -> None:
        self._schedule("model.completed", self._components.pop(run_id, "unknown"))

    def on_llm_error(self, _error: BaseException, *, run_id: UUID, **_kwargs: Any) -> None:
        self._schedule("model.failed", self._components.pop(run_id, "unknown"))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        _input_str: str,
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        component = str(serialized.get("name", "unknown"))
        self._components[run_id] = component
        self._schedule("tool.started", component)

    def on_tool_end(self, _output: Any, *, run_id: UUID, **_kwargs: Any) -> None:
        self._schedule("tool.completed", self._components.pop(run_id, "unknown"))

    def on_tool_error(self, _error: BaseException, *, run_id: UUID, **_kwargs: Any) -> None:
        self._schedule("tool.failed", self._components.pop(run_id, "unknown"))

    async def flush(self) -> None:
        """Wait until all callback events have reached the ordered runtime sink."""
        if self._pending:
            await asyncio.gather(*(asyncio.wrap_future(pending) for pending in self._pending))
            self._pending.clear()

    def _schedule(self, event_type: str, component: str) -> None:
        self._pending.append(
            asyncio.run_coroutine_threadsafe(
                self.sink.emit(event_type, {"component": component}),
                self._loop,
            )
        )
