import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from heart_of_the_swarm.observability.audit import audit_event
from heart_of_the_swarm.observability.events import (
    ModelUsageEvent,
    TrajectoryEvent,
    json_value,
    serialized_size,
    string_id,
    workflow_context,
)


class RuntimeCallbackHandler(BaseCallbackHandler):
    """Collect model usage and the observable model/tool execution trajectory."""

    def __init__(self, stage: str, provider: str = "", model_id: str = "") -> None:
        self.stage = stage
        self.provider = provider
        self.model_id = model_id
        self.events: list[ModelUsageEvent] = []
        self.trajectory: list[TrajectoryEvent] = []
        self._started: dict[UUID, float] = {}
        self._components: dict[UUID, str] = {}
        self._model_context: dict[UUID, dict[str, Any]] = {}
        self._tool_started: dict[UUID, float] = {}
        self._tool_context: dict[UUID, dict[str, Any]] = {}

    def record(
        self,
        event_type: str,
        *,
        component: str | None = None,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.trajectory.append(
            TrajectoryEvent(
                sequence=len(self.trajectory) + 1,
                event_type=event_type,
                component=component,
                langchain_run_id=string_id(run_id),
                parent_run_id=string_id(parent_run_id),
                payload=json_value(payload or {}),
                created_at=datetime.now(UTC),
            )
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = perf_counter()
        identifier = str(serialized.get("name") or serialized.get("id", ["unknown"])[-1])
        context = workflow_context(kwargs.get("metadata"))
        self._components[run_id] = identifier
        self._model_context[run_id] = context
        self.record(
            "model.started",
            component=identifier,
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={**context, "messages": messages},
        )
        audit_event(
            "llm.started",
            component=identifier,
            message_batches=len(messages),
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        output = response.llm_output or {}
        usage = output.get("token_usage") or output.get("usage") or {}
        response_metadata = {}
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
            response_metadata = getattr(message, "response_metadata", None) or {}
            if not usage:
                usage = getattr(message, "usage_metadata", None) or {}
                usage = (
                    usage
                    or response_metadata.get("token_usage")
                    or response_metadata.get("usage")
                    or {}
                )
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        latency_ms = round((perf_counter() - self._started.pop(run_id, perf_counter())) * 1000, 2)
        component = self._components.pop(run_id, None)
        context = self._model_context.pop(run_id, {})
        self.record(
            "model.completed",
            component=component,
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={
                **context,
                "generations": response.generations,
                "metadata": output,
                "latency_ms": latency_ms,
            },
        )
        self.events.append(
            ModelUsageEvent(
                stage=self.stage,
                provider=self.provider,
                model_id=self.model_id,
                resolved_provider=response_metadata.get("provider"),
                resolved_model_id=(
                    output.get("model_name")
                    or output.get("model")
                    or response_metadata.get("model_name")
                    or response_metadata.get("model")
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost=float(
                    usage.get("cost", output.get("cost", response_metadata.get("cost", 0))) or 0
                ),
                latency_ms=latency_ms,
            )
        )
        audit_event(
            "llm.completed",
            generations=sum(len(batch) for batch in response.generations),
            token_usage=usage,
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        latency_ms = round((perf_counter() - self._started.pop(run_id, perf_counter())) * 1000, 2)
        component = self._components.pop(run_id, None)
        context = self._model_context.pop(run_id, {})
        self.record(
            "model.failed",
            component=component,
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={
                **context,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "latency_ms": latency_ms,
            },
        )
        self.events.append(
            ModelUsageEvent(
                stage=self.stage,
                provider=self.provider,
                model_id=self.model_id,
                latency_ms=latency_ms,
                success=False,
                error_type=type(error).__name__,
            )
        )
        audit_event(
            "llm.failed",
            level=logging.ERROR,
            error_type=type(error).__name__,
            error_message=str(error),
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        component = serialized.get("name", "unknown")
        context = workflow_context(kwargs.get("metadata"))
        self._components[run_id] = component
        self._tool_started[run_id] = perf_counter()
        self._tool_context[run_id] = context
        self.record(
            "tool.started",
            component=component,
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={**context, "input": kwargs.get("inputs", input_str)},
        )
        audit_event(
            "tool.started",
            tool=component,
            input_characters=len(input_str),
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        latency_ms = round(
            (perf_counter() - self._tool_started.pop(run_id, perf_counter())) * 1000, 2
        )
        context = self._tool_context.pop(run_id, {})
        self.record(
            "tool.completed",
            component=self._components.pop(run_id, None),
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={**context, "output": output, "latency_ms": latency_ms},
        )
        audit_event(
            "tool.completed",
            output_characters=serialized_size(output),
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        latency_ms = round(
            (perf_counter() - self._tool_started.pop(run_id, perf_counter())) * 1000, 2
        )
        context = self._tool_context.pop(run_id, {})
        self.record(
            "tool.failed",
            component=self._components.pop(run_id, None),
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={
                **context,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "latency_ms": latency_ms,
            },
        )
        audit_event(
            "tool.failed",
            level=logging.ERROR,
            error_type=type(error).__name__,
            error_message=str(error),
            langchain_run_id=str(run_id),
            parent_run_id=string_id(parent_run_id),
            **context,
        )
