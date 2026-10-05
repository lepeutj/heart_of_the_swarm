import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

import heart_of_the_swarm.observability.audit as audit_module
from heart_of_the_swarm.observability import (
    RuntimeCallbackHandler,
    audit_event,
    audit_exception,
    configure_audit_logging,
    trace_context,
)


@pytest.fixture
def audit_file():
    for handler in audit_module._logger.handlers:
        handler.close()
    audit_module._logger.handlers.clear()
    audit_module._configured_target = None
    target = Path("logs") / f"test-audit-{uuid4()}.jsonl"
    configure_audit_logging(str(target))
    yield target
    for handler in audit_module._logger.handlers:
        handler.close()
    audit_module._logger.handlers.clear()
    audit_module._configured_target = None
    target.unlink(missing_ok=True)


def flush_handlers() -> None:
    for handler in audit_module._logger.handlers:
        handler.flush()


def test_audit_log_writes_structured_trace(audit_file) -> None:
    with trace_context("trace-123"):
        audit_event("test.completed", value=42)
    flush_handlers()

    entry = json.loads(audit_file.read_text(encoding="utf-8"))
    assert entry["event"] == "test.completed"
    assert entry["trace_id"] == "trace-123"
    assert entry["value"] == 42
    assert entry["level"] == "INFO"


def test_failures_include_stack_trace_without_payload_content(audit_file) -> None:
    with trace_context("trace-failure"):
        try:
            raise RuntimeError("controlled failure")
        except RuntimeError:
            audit_exception("test.failed", payload_characters=100)
    flush_handlers()

    entry = json.loads(audit_file.read_text(encoding="utf-8"))
    assert entry["event"] == "test.failed"
    assert entry["trace_id"] == "trace-failure"
    assert entry["payload_characters"] == 100
    assert "RuntimeError: controlled failure" in entry["exception"]


def test_model_callback_collects_usage(audit_file) -> None:
    handler = RuntimeCallbackHandler("agent", "openrouter", "vendor/model")
    run_id = uuid4()
    handler.on_chat_model_start({"name": "test"}, [[]], run_id=run_id)
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="done",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 4,
                            "total_tokens": 16,
                        },
                    )
                )
            ]
        ]
    )
    handler.on_llm_end(response, run_id=run_id)

    assert handler.events[0].provider == "openrouter"
    assert handler.events[0].total_tokens == 16
    assert handler.events[0].success is True
