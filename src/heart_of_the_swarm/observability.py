import json
import logging
import logging.handlers
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_logger = logging.getLogger("heart_of_the_swarm.audit")
_configure_lock = Lock()
_configured_target: Path | None = None


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "trace_id": getattr(record, "trace_id", None),
        }
        entry.update(getattr(record, "event_fields", {}))
        if record.exc_info:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_audit_logging(
    log_file: str,
    level: str = "INFO",
    max_bytes: int = 10_000_000,
    backup_count: int = 5,
) -> Path:
    """Configure a process-wide rotating JSONL audit log exactly once."""
    global _configured_target

    target = Path(log_file).expanduser().resolve()
    with _configure_lock:
        if _configured_target is not None:
            return _configured_target
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        handler.setFormatter(JsonLineFormatter())
        _logger.handlers.clear()
        _logger.addHandler(handler)
        _logger.setLevel(level.upper())
        _logger.propagate = False
        _configured_target = target
    return target


def get_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_context(trace_id: str | None = None):
    existing = get_trace_id()
    if existing:
        yield existing
        return

    assigned = trace_id or str(uuid4())
    token = _trace_id.set(assigned)
    try:
        yield assigned
    finally:
        _trace_id.reset(token)


def audit_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    _logger.log(
        level,
        event,
        extra={"event": event, "trace_id": get_trace_id(), "event_fields": fields},
    )


def audit_exception(event: str, **fields: Any) -> None:
    _logger.error(
        event,
        extra={"event": event, "trace_id": get_trace_id(), "event_fields": fields},
        exc_info=True,
    )


def _size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, default=str))
    except TypeError:
        return len(str(value))


@dataclass
class ModelUsageEvent:
    stage: str
    provider: str
    model_id: str
    resolved_provider: str | None = None
    resolved_model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0
    latency_ms: float = 0
    success: bool = True
    error_type: str | None = None


class RuntimeCallbackHandler(BaseCallbackHandler):
    """Record model and tool lifecycle events without recording their payload contents."""

    def __init__(self, stage: str, provider: str, model_id: str) -> None:
        self.stage = stage
        self.provider = provider
        self.model_id = model_id
        self.events: list[ModelUsageEvent] = []
        self._started: dict[UUID, float] = {}

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
        identifier = serialized.get("name") or serialized.get("id", ["unknown"])[-1]
        audit_event(
            "llm.started",
            component=identifier,
            message_batches=len(messages),
            langchain_run_id=str(run_id),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
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
            parent_run_id=str(parent_run_id) if parent_run_id else None,
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
            parent_run_id=str(parent_run_id) if parent_run_id else None,
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
        audit_event(
            "tool.started",
            tool=serialized.get("name", "unknown"),
            input_characters=len(input_str),
            langchain_run_id=str(run_id),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        audit_event(
            "tool.completed",
            output_characters=_size(output),
            langchain_run_id=str(run_id),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        audit_event(
            "tool.failed",
            level=logging.ERROR,
            error_type=type(error).__name__,
            error_message=str(error),
            langchain_run_id=str(run_id),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )
