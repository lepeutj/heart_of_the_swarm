from uuid import uuid4

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.artifacts import AgentArtifact, load_agent_artifact
from heart_of_the_swarm.authorization import (
    AuthorizationContext,
    AuthorizationService,
    CompositePolicySource,
    ConfiguredCapabilityPolicySource,
    ConfiguredCredentialPolicySource,
)
from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.credentials import (
    CredentialRef,
    CredentialResolver,
    LocalSecretStore,
    RuntimeIdentity,
)
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import RuntimeCallbackHandler, audit_event, trace_context
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeMetadata
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import MCPConnection, MCPToolLoader, create_default_registry
from heart_of_the_swarm.validator import AgentSpecValidator


class StandaloneAgentRuntime:
    """Database-independent runtime reconstructed from one exported AgentVersion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools = create_default_registry(settings)
        self.providers = ProviderRegistry(settings)
        self.validator = AgentSpecValidator(self.tools, self.providers)
        authorization = AuthorizationService(
            CompositePolicySource(
                ConfiguredCapabilityPolicySource(settings.capability_policies),
                ConfiguredCredentialPolicySource(settings.credential_policies),
            )
        )
        self.capability_authorizer = CapabilityAuthorizer(authorization)
        self.runtime_identity = RuntimeIdentity(id=settings.runtime_id)
        self.credential_resolver = CredentialResolver(
            self.runtime_identity,
            authorization,
            LocalSecretStore(settings.local_secrets),
        )
        mcp_sources = {
            name: MCPConnection(
                url=url,
                bearer_credential_ref=(
                    CredentialRef(id=settings.mcp_bearer_credentials[name])
                    if name in settings.mcp_bearer_credentials
                    else None
                ),
            )
            for name, url in settings.mcp_servers.items()
        }
        self.mcp_tools = MCPToolLoader(
            self.tools,
            mcp_sources,
            credential_resolver=self.credential_resolver,
        )
        self.runner = AgentRunner(
            self.providers,
            self.validator,
            AgentFactory(self.tools),
            self.capability_authorizer,
        )
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
            runtime_id=self.runtime_identity.id,
            agent={
                "name": artifact.agent.spec.name,
                "goal": artifact.agent.spec.goal,
                "model": artifact.agent.spec.model.model_dump(mode="json"),
                "tools": artifact.agent.spec.tools,
            },
            observability={
                "mlflow": self.telemetry.enabled,
                "langchain_autolog": self.telemetry.langchain_autolog_enabled,
            },
        )

    async def invoke(self, agent_input: str) -> InvokeResponse:
        if not isinstance(agent_input, str) or not 1 <= len(agent_input) <= 10_000:
            raise ValueError("agent input must be a non-empty string of at most 10000 characters")
        artifact = self._artifact()
        run_id = str(uuid4())
        trace_id = str(uuid4())
        mlflow_trace_id = None
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
            mlflow_trace_id = self.telemetry.trace_id(span)
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
                security=ExecutionSecurityContext(
                    principal=self.capability_authorizer.agent_version_principal(
                        artifact.agent.agent_version_id
                    ),
                    authorization=AuthorizationContext(run_id=run_id),
                ),
            )
            self.telemetry.set_outputs(span, {"output": output})
        return InvokeResponse(
            run_id=run_id,
            trace_id=trace_id,
            mlflow_trace_id=mlflow_trace_id,
            output=output,
        )

    def _artifact(self) -> AgentArtifact:
        if self.artifact is None:
            raise RuntimeError("standalone agent runtime is not initialized")
        return self.artifact
