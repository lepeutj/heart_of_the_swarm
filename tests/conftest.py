import pytest

from heart_of_the_swarm.config import get_settings


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    """Keep unit tests independent from the developer's local service configuration."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("MLFLOW_ENABLED", "false")
    monkeypatch.setenv("MCP_SERVERS", "{}")
    monkeypatch.setenv("CONNECTOR_DATABASES", "{}")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
