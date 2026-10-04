import sys
from contextlib import contextmanager
from typing import Any

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.observability import audit_event


class Telemetry:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.mlflow_enabled
        self.langchain_autolog_enabled = False
        self._mlflow = None
        if not self.enabled:
            return
        try:
            import mlflow

            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            mlflow.set_experiment(settings.mlflow_experiment)
            self._mlflow = mlflow
        except Exception as exc:
            self.enabled = False
            audit_event("mlflow.unavailable", error_type=type(exc).__name__, error_message=str(exc))
            return
        try:
            mlflow.langchain.autolog()
            self.langchain_autolog_enabled = True
        except Exception as exc:
            audit_event(
                "mlflow.langchain_autolog.unavailable",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any]):
        if not self.enabled or self._mlflow is None:
            yield None
            return
        context = None
        try:
            context = self._mlflow.start_span(name=name)
            span = context.__enter__()
            span.set_attributes(attributes)
        except Exception as exc:
            audit_event("mlflow.span.failed", error_type=type(exc).__name__, error_message=str(exc))
            yield None
            return
        try:
            yield span
        except BaseException:
            try:
                context.__exit__(*sys.exc_info())
            except Exception as exc:
                audit_event(
                    "mlflow.span.failed", error_type=type(exc).__name__, error_message=str(exc)
                )
            raise
        else:
            try:
                context.__exit__(None, None, None)
            except Exception as exc:
                audit_event(
                    "mlflow.span.failed", error_type=type(exc).__name__, error_message=str(exc)
                )

    @staticmethod
    def set_inputs(span: Any, inputs: dict[str, Any]) -> None:
        try:
            if span is not None:
                span.set_inputs(inputs)
        except Exception as exc:
            audit_event("mlflow.span.failed", error_type=type(exc).__name__, error_message=str(exc))

    @staticmethod
    def set_outputs(span: Any, outputs: dict[str, Any]) -> None:
        try:
            if span is not None:
                span.set_outputs(outputs)
        except Exception as exc:
            audit_event("mlflow.span.failed", error_type=type(exc).__name__, error_message=str(exc))

    @staticmethod
    def trace_id(span: Any) -> str | None:
        """Return the MLflow trace identifier for an active span when available."""
        trace_id = getattr(span, "trace_id", None)
        return trace_id if isinstance(trace_id, str) else None
