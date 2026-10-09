import asyncio
from typing import Any, Protocol
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler


class ModelCallBudgetExceededError(RuntimeError):
    """Signal that the next model call would exceed the logical execution budget."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Model-call budget of {limit} has been exhausted.")


class ModelCallBudget(Protocol):
    async def consume_model_call(self) -> int:
        """Reserve one model call and return the thread-wide consumed count."""


class InMemoryModelCallBudget:
    """Provide the same call-limit contract to runtimes without product persistence."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.consumed = 0
        self._lock = asyncio.Lock()

    async def consume_model_call(self) -> int:
        async with self._lock:
            if self.consumed >= self.limit:
                raise ModelCallBudgetExceededError(self.limit)
            self.consumed += 1
            return self.consumed


class ModelCallBudgetCallback(AsyncCallbackHandler):
    """Reserve budget immediately before LangChain starts a model provider call."""

    raise_error = True

    def __init__(self, budget: ModelCallBudget) -> None:
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
