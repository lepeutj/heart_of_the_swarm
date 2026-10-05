import json
import logging
import logging.handlers
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_logger = logging.getLogger("heart_of_the_swarm.audit")
_configure_lock = Lock()
_configured_target: Path | None = None


class JsonLineFormatter(logging.Formatter):
    """Serialize one operational audit record as JSON Lines."""

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
