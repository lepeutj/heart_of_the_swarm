from langchain_openai import ChatOpenAI

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.spec import ModelConfig, ModelDescriptor


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings.openai_api_key is not None

    async def list_models(self) -> list[ModelDescriptor]:
        model_ids = {self.settings.builder_model, self.settings.default_agent_model}
        model_ids.update(
            item.split(":", 1)[1]
            for item in self.settings.allowed_agent_models
            if item.startswith("openai:")
        )
        return [
            ModelDescriptor(
                provider=self.name,
                model_id=model_id,
                name=model_id,
                supports_tools=True,
                supports_structured_output=True,
            )
            for model_id in sorted(model_ids)
        ]

    def create_model(self, config: ModelConfig) -> ChatOpenAI:
        if not self.settings.openai_api_key:
            raise ValueError("OpenAI is not configured")
        kwargs = {
            "model": config.model_id,
            "api_key": self.settings.openai_api_key,
            "temperature": config.temperature,
        }
        if config.max_tokens:
            kwargs["max_tokens"] = config.max_tokens
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url
        return ChatOpenAI(**kwargs)
