from heart_of_the_swarm.builder import AgentBuilder, render_builder_prompt
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.factory import AGENT_SYSTEM_PROMPT_VERSION, render_system_prompt
from heart_of_the_swarm.observability import (
    RuntimeCallbackHandler,
    get_trace_id,
    trace_context,
)
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.repositories import AgentRepository, ObservabilityRepository
from heart_of_the_swarm.spec import (
    AgentDetail,
    AgentSpec,
    AgentSummary,
    DesignRequest,
    DesignResponse,
    ModelConfig,
    UsageSummary,
)
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.validator import AgentSpecValidator


class AgentService:
    def __init__(
        self,
        settings: Settings,
        tools: ToolRegistry,
        providers: ProviderRegistry,
        validator: AgentSpecValidator,
        database: Database,
        telemetry: Telemetry,
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.providers = providers
        self.validator = validator
        self.database = database
        self.telemetry = telemetry

    async def design(self, request: DesignRequest) -> DesignResponse:
        with (
            trace_context() as trace_id,
            self.telemetry.span("agent.design", {"app.trace_id": get_trace_id() or ""}) as span,
        ):
            builder_config = ModelConfig(
                provider=self.settings.builder_provider,
                model_id=self.settings.builder_model,
            )
            usage = RuntimeCallbackHandler(
                "builder", builder_config.provider, builder_config.model_id
            )
            builder_prompt = render_builder_prompt(request.task, self.tools.names)
            self.telemetry.set_inputs(
                span,
                {
                    "task": request.task,
                    "builder_prompt": builder_prompt,
                    "provider": builder_config.provider,
                    "model": builder_config.model_id,
                },
            )
            async with self.database.session() as session:
                design_id = await AgentRepository(session).start_design(
                    trace_id,
                    request.task,
                    builder_config,
                    builder_prompt,
                )
            spec = None
            try:
                builder_models = await self.providers.list_models(
                    builder_config.provider, enforce_policy=False
                )
                builder_descriptor = next(
                    (
                        model
                        for model in builder_models
                        if model.model_id == builder_config.model_id
                    ),
                    None,
                )
                if builder_descriptor is None or not builder_descriptor.supports_structured_output:
                    raise ValueError("builder model must support structured output")
                builder = AgentBuilder(
                    self.providers.create_model(builder_config), self.tools.names
                )
                draft = await builder.build(request.task, callbacks=[usage])
                spec = AgentSpec(
                    **draft.model_dump(),
                    model=ModelConfig(
                        provider=request.provider or self.settings.default_agent_provider,
                        model_id=request.model_id or self.settings.default_agent_model,
                    ),
                )
                await self.validator.validate(spec)
                async with self.database.session() as session:
                    await AgentRepository(session).complete_design(design_id, spec)
                self.telemetry.set_outputs(span, {"agent_spec": spec.model_dump(mode="json")})
                return DesignResponse(design_id=design_id, trace_id=trace_id, spec=spec)
            except Exception as exc:
                async with self.database.session() as session:
                    await AgentRepository(session).fail_design(design_id, exc, spec)
                raise
            finally:
                async with self.database.session() as session:
                    await ObservabilityRepository(session).add_usage(usage.events, trace_id)

    async def validation_errors(self, spec: AgentSpec) -> list[str]:
        return await self.validator.errors(spec)

    async def create_agent(self, spec: AgentSpec) -> AgentDetail:
        await self.validator.validate(spec)
        async with self.database.session() as session:
            return await AgentRepository(session).create(
                spec, AGENT_SYSTEM_PROMPT_VERSION, render_system_prompt(spec)
            )

    async def list_agents(self) -> list[AgentSummary]:
        async with self.database.session() as session:
            return await AgentRepository(session).list_agents()

    async def add_version(self, agent_id: str, spec: AgentSpec) -> AgentDetail | None:
        await self.validator.validate(spec)
        async with self.database.session() as session:
            return await AgentRepository(session).add_version(
                agent_id, spec, AGENT_SYSTEM_PROMPT_VERSION, render_system_prompt(spec)
            )

    async def get_agent(self, agent_id: str) -> AgentDetail | None:
        async with self.database.session() as session:
            return await AgentRepository(session).get(agent_id)

    async def usage_summary(self) -> list[UsageSummary]:
        async with self.database.session() as session:
            return await ObservabilityRepository(session).usage_summary()
