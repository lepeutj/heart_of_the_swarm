import time

import httpx
from langchain_openai import ChatOpenAI

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.spec import ModelConfig, ModelDescriptor


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._models: list[ModelDescriptor] = []
        self._loaded_at = 0.0

    @property
    def configured(self) -> bool:
        return self.settings.openrouter_api_key is not None

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise ValueError("OpenRouter is not configured")
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key.get_secret_value()}",
            "X-Title": self.settings.openrouter_app_name,
        }
        if self.settings.openrouter_site_url:
            headers["HTTP-Referer"] = self.settings.openrouter_site_url
        return headers

    async def list_models(self) -> list[ModelDescriptor]:
        if (
            self._models
            and time.monotonic() - self._loaded_at < self.settings.model_catalog_ttl_seconds
        ):
            return self._models
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(
                f"{self.settings.openrouter_base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
        models = []
        for item in response.json().get("data", []):
            model_id = item["id"]
            parameters = set(item.get("supported_parameters") or [])
            pricing = item.get("pricing") or {}
            models.append(
                ModelDescriptor(
                    provider=self.name,
                    model_id=model_id,
                    name=item.get("name") or model_id,
                    context_length=item.get("context_length"),
                    prompt_price=pricing.get("prompt"),
                    completion_price=pricing.get("completion"),
                    supports_tools="tools" in parameters,
                    supports_structured_output=bool(
                        {"response_format", "structured_outputs"} & parameters
                    ),
                )
            )
        self._models = sorted(models, key=lambda model: model.name.lower())
        self._loaded_at = time.monotonic()
        return self._models

    def create_model(self, config: ModelConfig) -> ChatOpenAI:
        kwargs = {
            "model": config.model_id,
            "api_key": self.settings.openrouter_api_key,
            "base_url": self.settings.openrouter_base_url,
            "temperature": config.temperature,
            "default_headers": self._headers(),
        }
        if config.max_tokens:
            kwargs["max_tokens"] = config.max_tokens
        return ChatOpenAI(**kwargs)
