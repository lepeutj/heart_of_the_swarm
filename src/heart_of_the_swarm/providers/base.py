from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from heart_of_the_swarm.spec import ModelConfig, ModelDescriptor


class ModelProvider(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def list_models(self) -> list[ModelDescriptor]: ...

    def create_model(self, config: ModelConfig) -> BaseChatModel: ...
