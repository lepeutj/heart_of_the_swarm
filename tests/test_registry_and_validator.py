import pytest
from pydantic import ValidationError

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.validator import AgentSpecValidator, SpecValidationError


class FakeProviders:
    def __init__(self, errors: list[str] | None = None) -> None:
        self._errors = errors or []

    async def validate(self, spec: AgentSpec) -> list[str]:
        return self._errors


def make_spec(**overrides: object) -> AgentSpec:
    values = {
        "name": "ResearchAgent",
        "goal": "Research a topic",
        "tools": ["web_search", "document_reader"],
        "instructions": "Search, read primary sources, and summarize them.",
        "model": {"provider": "test", "model_id": "test-model"},
    }
    values.update(overrides)
    return AgentSpec.model_validate(values)


def test_registry_contains_exactly_three_tools() -> None:
    assert create_default_registry().names == ("web_search", "calculator", "document_reader")


async def test_validator_accepts_registered_tools_and_model() -> None:
    spec = make_spec()
    validator = AgentSpecValidator(create_default_registry(), FakeProviders())
    assert await validator.validate(spec) == spec


async def test_validator_rejects_unknown_tool() -> None:
    validator = AgentSpecValidator(create_default_registry(), FakeProviders())
    with pytest.raises(SpecValidationError, match="unknown tools"):
        await validator.validate(make_spec(tools=["shell"]))


async def test_validator_rejects_provider_error() -> None:
    validator = AgentSpecValidator(
        create_default_registry(), FakeProviders(["selected model does not support tool calling"])
    )
    with pytest.raises(SpecValidationError, match="does not support tool calling"):
        await validator.validate(make_spec())


def test_settings_parse_comma_separated_allowed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_AGENT_MODELS", "openai:small,openrouter:vendor/large")
    parsed = Settings()
    assert parsed.allowed_agent_models == ["openai:small", "openrouter:vendor/large"]


def test_settings_reject_unsafe_worker_timing() -> None:
    with pytest.raises(ValidationError, match="heartbeat must be less than half"):
        Settings(worker_heartbeat_seconds=30, worker_lease_seconds=60)
