import json
from typing import Any


class WorkflowStateBudgetError(ValueError):
    """Describe a safe, deterministic workflow-state budget violation."""

    def __init__(self, code: str, message: str, *, observed_bytes: int | None = None) -> None:
        self.code = code
        self.observed_bytes = observed_bytes
        super().__init__(message)


def enforce_state_size(value: Any, max_bytes: int) -> int:
    """Measure canonical JSON incrementally and stop once the state exceeds its budget."""
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    total = 0
    try:
        for chunk in encoder.iterencode(value):
            total += len(chunk.encode("utf-8"))
            if total > max_bytes:
                raise WorkflowStateBudgetError(
                    "workflow.execution.state_budget_exceeded",
                    f"Workflow state exceeds the configured limit of {max_bytes} bytes.",
                    observed_bytes=total,
                )
    except WorkflowStateBudgetError:
        raise
    except (TypeError, ValueError) as exc:
        raise WorkflowStateBudgetError(
            "workflow.execution.state_not_serializable",
            "Workflow state must remain JSON serializable.",
        ) from exc
    return total
