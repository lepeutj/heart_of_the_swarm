import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.providers.base import ModelProvider
from heart_of_the_swarm.providers.openai import OpenAIProvider
from heart_of_the_swarm.providers.openrouter import OpenRouterProvider
from heart_of_the_swarm.spec import AgentSpec, ModelConfig, ModelDescriptor


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        providers: list[ModelProvider] = [OpenAIProvider(settings), OpenRouterProvider(settings)]
        self._providers = {provider.name: provider for provider in providers}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def statuses(self) -> list[dict[str, str | bool]]:
        return [
            {"id": name, "configured": provider.configured}
            for name, provider in self._providers.items()
        ]

    def get(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"unknown model provider: {name}") from exc

    async def list_models(
        self, provider: str, *, enforce_policy: bool = True
    ) -> list[ModelDescriptor]:
        selected = self.get(provider)
        if not selected.configured:
            raise ValueError(f"{provider} is not configured")
        models = await selected.list_models()
        if not enforce_policy or not self.settings.allowed_agent_models:
            return models
        allowed = set(self.settings.allowed_agent_models)
        return [model for model in models if f"{provider}:{model.model_id}" in allowed]

    def create_model(self, config: ModelConfig) -> BaseChatModel:
        return self.get(config.provider).create_model(config)

    async def validate(self, spec: AgentSpec) -> list[str]:
        try:
            models = await self.list_models(spec.model.provider)
        except (ValueError, httpx.HTTPError) as exc:
            return [str(exc)]
        descriptor = next(
            (model for model in models if model.model_id == spec.model.model_id), None
        )
        if descriptor is None:
            return ["selected model is not available or allowed"]
        if spec.tools and not descriptor.supports_tools:
            return ["selected model does not support tool calling"]
        return []

    def validate_execution(self, spec: AgentSpec) -> list[str]:
        return self.validate_model_execution(spec.model)

    def validate_model_execution(self, config: ModelConfig) -> list[str]:
        try:
            provider = self.get(config.provider)
        except ValueError as exc:
            return [str(exc)]
        if not provider.configured:
            return [f"{config.provider} is not configured"]
        if self.settings.allowed_agent_models:
            selected = f"{config.provider}:{config.model_id}"
            if selected not in self.settings.allowed_agent_models:
                return ["selected model is not allowed"]
        return []
