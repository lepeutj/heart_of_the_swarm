import logging

from heart_of_the_swarm.observability import audit_event
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.spec import AgentSpec
from heart_of_the_swarm.tools import ToolRegistry


class SpecValidationError(ValueError):
    pass


class AgentSpecValidator:
    def __init__(self, tools: ToolRegistry, providers: ProviderRegistry) -> None:
        self.tools = tools
        self.providers = providers

    async def errors(self, spec: AgentSpec) -> list[str]:
        errors = []
        unknown = sorted(set(spec.tools) - set(self.tools.names))
        if unknown:
            errors.append(f"unknown tools: {', '.join(unknown)}")
        errors.extend(await self.providers.validate(spec))
        return errors

    async def validate(self, spec: AgentSpec) -> AgentSpec:
        audit_event("validator.started", agent_name=spec.name)
        errors = await self.errors(spec)
        if errors:
            audit_event(
                "validator.rejected", level=logging.WARNING, agent_name=spec.name, errors=errors
            )
            raise SpecValidationError("; ".join(errors))
        audit_event(
            "validator.completed",
            provider=spec.model.provider,
            model=spec.model.model_id,
            tools=spec.tools,
        )
        return spec

    def validate_execution(self, spec: AgentSpec) -> AgentSpec:
        unknown = sorted(set(spec.tools) - set(self.tools.names))
        errors = [f"unknown tools: {', '.join(unknown)}"] if unknown else []
        errors.extend(self.providers.validate_execution(spec))
        if errors:
            raise SpecValidationError("; ".join(errors))
        return spec
