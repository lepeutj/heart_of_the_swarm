import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


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


@dataclass(frozen=True)
class TrajectoryEvent:
    sequence: int
    event_type: str
    component: str | None
    langchain_run_id: str | None
    parent_run_id: str | None
    payload: dict[str, Any]
    created_at: datetime


def json_value(value: Any) -> Any:
    """Convert callback values into persistence-safe JSON data."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="json", serialize_as_any=True))
    return str(value)


def workflow_context(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    keys = ("workflow_id", "workflow_run_id", "node_id", "node_type")
    return {key: metadata[key] for key in keys if key in metadata}


def serialized_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, default=str))
    except TypeError:
        return len(str(value))


def string_id(value: UUID | None) -> str | None:
    return str(value) if value else None
