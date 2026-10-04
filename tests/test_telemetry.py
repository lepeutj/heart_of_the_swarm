import sys
from types import SimpleNamespace
from typing import Any

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.telemetry import Telemetry


class FakeSpan:
    trace_id = "mlflow-trace-123"

    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.inputs: dict[str, Any] | None = None
        self.outputs: dict[str, Any] | None = None

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes = attributes

    def set_inputs(self, inputs: dict[str, Any]) -> None:
        self.inputs = inputs

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class FakeSpanContext:
    def __init__(self, span: FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> FakeSpan:
        return self.span

    def __exit__(self, *_args: object) -> None:
        return None


def fake_mlflow(*, autolog_error: Exception | None = None):
    calls: list[tuple[str, object]] = []
    span = FakeSpan()

    def autolog() -> None:
        calls.append(("autolog", None))
        if autolog_error is not None:
            raise autolog_error

    module = SimpleNamespace(
        set_tracking_uri=lambda value: calls.append(("tracking_uri", value)),
        set_experiment=lambda value: calls.append(("experiment", value)),
        start_span=lambda name: FakeSpanContext(span),
        langchain=SimpleNamespace(autolog=autolog),
    )
    return module, span, calls


def test_telemetry_enables_langchain_autolog_and_exposes_mlflow_trace_id(monkeypatch) -> None:
    mlflow, expected_span, calls = fake_mlflow()
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    telemetry = Telemetry(
        Settings(
            mlflow_enabled=True,
            mlflow_tracking_uri="http://tracking.test",
            mlflow_experiment="runtime-test",
        )
    )

    with telemetry.span("workflow.run", {"run.id": "run-1"}) as span:
        telemetry.set_inputs(span, {"input": {"value": 1}})
        telemetry.set_outputs(span, {"output": {"value": 2}})
        trace_id = telemetry.trace_id(span)

    assert telemetry.enabled is True
    assert telemetry.langchain_autolog_enabled is True
    assert calls == [
        ("tracking_uri", "http://tracking.test"),
        ("experiment", "runtime-test"),
        ("autolog", None),
    ]
    assert expected_span.attributes == {"run.id": "run-1"}
    assert expected_span.inputs == {"input": {"value": 1}}
    assert expected_span.outputs == {"output": {"value": 2}}
    assert trace_id == "mlflow-trace-123"


def test_autolog_failure_keeps_manual_mlflow_tracing_available(monkeypatch) -> None:
    mlflow, _, _ = fake_mlflow(autolog_error=RuntimeError("unsupported integration"))
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)

    telemetry = Telemetry(Settings(mlflow_enabled=True))

    assert telemetry.enabled is True
    assert telemetry.langchain_autolog_enabled is False
    with telemetry.span("workflow.run", {}) as span:
        assert telemetry.trace_id(span) == "mlflow-trace-123"
