import asyncio
from typing import Any, Protocol
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler


class ExecutionBudgetExceededError(RuntimeError):
    """Signal that the next model or tool call would exceed its execution budget."""

    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"{resource.replace('_', ' ').title()} budget of {limit} is exhausted.")


class ExecutionBudget(Protocol):
    async def consume_model_call(self) -> int:
        """Reserve one model call and return the thread-wide consumed count."""

    async def consume_tool_call(self) -> int:
        """Reserve one tool call and return the thread-wide consumed count."""


class InMemoryExecutionBudget:
    """Provide the same call-limit contract to runtimes without product persistence."""

    def __init__(self, max_model_calls: int, max_tool_calls: int) -> None:
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.model_calls = 0
        self.tool_calls = 0
        self._lock = asyncio.Lock()

    async def consume_model_call(self) -> int:
        async with self._lock:
            if self.model_calls >= self.max_model_calls:
                raise ExecutionBudgetExceededError("model_calls", self.max_model_calls)
            self.model_calls += 1
            return self.model_calls

    async def consume_tool_call(self) -> int:
        async with self._lock:
            if self.tool_calls >= self.max_tool_calls:
                raise ExecutionBudgetExceededError("tool_calls", self.max_tool_calls)
            self.tool_calls += 1
            return self.tool_calls


class ModelCallBudgetCallback(AsyncCallbackHandler):
    """Reserve budget immediately before LangChain starts a model provider call."""

    raise_error = True

    def __init__(self, budget: ExecutionBudget) -> None:
        self.budget = budget
        self._counted_runs: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def on_chat_model_start(
        self,
        _serialized: dict[str, Any],
        _messages: list[list[Any]],
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        await self._consume_once(run_id)

    async def on_llm_start(
        self,
        _serialized: dict[str, Any],
        _prompts: list[str],
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        await self._consume_once(run_id)

    async def _consume_once(self, run_id: UUID) -> None:
        # Some model adapters may report both generic and chat start hooks for one run.
        async with self._lock:
            if run_id in self._counted_runs:
                return
            await self.budget.consume_model_call()
            self._counted_runs.add(run_id)
