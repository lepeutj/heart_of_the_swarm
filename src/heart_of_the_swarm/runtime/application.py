from uuid import uuid4

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.artifacts import AgentArtifact, load_agent_artifact
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import RuntimeCallbackHandler, audit_event, trace_context
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeMetadata
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import MCPToolLoader, create_default_registry
from heart_of_the_swarm.validator import AgentSpecValidator


class StandaloneAgentRuntime:
    """Database-independent runtime reconstructed from one exported AgentVersion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools = create_default_registry(settings)
        self.mcp_tools = MCPToolLoader(self.tools, settings.mcp_servers)
        self.providers = ProviderRegistry(settings)
        self.validator = AgentSpecValidator(self.tools, self.providers)
        self.runner = AgentRunner(self.providers, self.validator, AgentFactory(self.tools))
        self.telemetry = Telemetry(settings)
        self.artifact: AgentArtifact | None = None

    async def initialize(self) -> None:
        await self.mcp_tools.load()
        try:
            artifact = load_agent_artifact(self.settings.agent_artifact_dir)
            self.validator.validate_execution(artifact.agent.spec)
            self.artifact = artifact
            audit_event(
                "runtime.ready",
                agent_id=str(artifact.agent.agent_id),
                agent_version_id=str(artifact.agent.agent_version_id),
            )
        except Exception:
            await self.mcp_tools.close()
            raise

    async def close(self) -> None:
        await self.mcp_tools.close()

    def metadata(self) -> RuntimeMetadata:
        artifact = self._artifact()
        return RuntimeMetadata(
            manifest=artifact.manifest,
            agent={
                "name": artifact.agent.spec.name,
                "goal": artifact.agent.spec.goal,
                "model": artifact.agent.spec.model.model_dump(mode="json"),
                "tools": artifact.agent.spec.tools,
            },
        )

    async def invoke(self, agent_input: str) -> InvokeResponse:
        if not isinstance(agent_input, str) or not 1 <= len(agent_input) <= 10_000:
            raise ValueError("agent input must be a non-empty string of at most 10000 characters")
        artifact = self._artifact()
        run_id = str(uuid4())
        trace_id = str(uuid4())
        callback = RuntimeCallbackHandler(
            "deployed_agent",
            artifact.agent.spec.model.provider,
            artifact.agent.spec.model.model_id,
        )
        with (
            trace_context(trace_id),
            self.telemetry.span(
                "deployed_agent.run",
                {
                    "app.trace_id": trace_id,
                    "run.id": run_id,
                    "agent.id": str(artifact.agent.agent_id),
                    "agent.version.id": str(artifact.agent.agent_version_id),
                    "agent.version": artifact.agent.version,
                },
            ) as span,
        ):
            self.telemetry.set_inputs(span, {"input": agent_input})
            output = await self.runner.invoke(
                artifact.agent.spec,
                agent_input,
                system_prompt=artifact.agent.system_prompt,
                callbacks=[callback],
                metadata={
                    "run_id": run_id,
                    "agent_id": str(artifact.agent.agent_id),
                    "agent_version_id": str(artifact.agent.agent_version_id),
                },
            )
            self.telemetry.set_outputs(span, {"output": output})
        return InvokeResponse(run_id=run_id, trace_id=trace_id, output=output)

    def _artifact(self) -> AgentArtifact:
        if self.artifact is None:
            raise RuntimeError("standalone agent runtime is not initialized")
        return self.artifact
