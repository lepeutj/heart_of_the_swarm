from pydantic import SecretStr

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.spec import AgentSpec


async def test_openai_catalog_validates_tool_capability() -> None:
    settings = Settings(
        openai_api_key=SecretStr("test"),
        builder_model="builder-model",
        default_agent_model="agent-model",
    )
    providers = ProviderRegistry(settings)
    models = await providers.list_models("openai")
    assert {model.model_id for model in models} == {"agent-model", "builder-model"}

    spec = AgentSpec(
        name="TestAgent",
        goal="Test the provider",
        tools=["calculator"],
        instructions="Calculate accurately.",
        model={"provider": "openai", "model_id": "agent-model"},
    )
    assert await providers.validate(spec) == []
