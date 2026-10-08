from typing import Any
from uuid import uuid4

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.artifacts import WorkflowArtifact, load_workflow_artifact
from heart_of_the_swarm.authorization import (
    AuthorizationService,
    CompositePolicySource,
    ConfiguredCapabilityPolicySource,
    ConfiguredCredentialPolicySource,
)
from heart_of_the_swarm.capability_authorization import CapabilityAuthorizer
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.credentials import CredentialResolver, LocalSecretStore, RuntimeIdentity
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import audit_event, trace_context
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.runtime.models import InvokeResponse, RuntimeMetadata
from heart_of_the_swarm.skills import SkillRegistry
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.validator import AgentSpecValidator
from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    WorkflowGraphFactory,
    WorkflowValidator,
    WorkflowVersionRunner,
)
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot


class StandaloneWorkflowRuntime:
    """Database-independent runtime reconstructed from one exported WorkflowVersion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools = create_default_registry(settings)
        self.skills = SkillRegistry(settings.skills_dir)
        self.providers = ProviderRegistry(settings)
        authorization = AuthorizationService(
            CompositePolicySource(
                ConfiguredCapabilityPolicySource(settings.capability_policies),
                ConfiguredCredentialPolicySource(settings.credential_policies),
            )
        )
        capability_authorizer = CapabilityAuthorizer(authorization)
        self.runtime_identity = RuntimeIdentity(id=settings.runtime_id)
        self.credential_resolver = CredentialResolver(
            self.runtime_identity,
            authorization,
            LocalSecretStore(settings.local_secrets),
        )
        agent_validator = AgentSpecValidator(self.tools, self.providers, self.skills)
        agent_runner = AgentRunner(
            self.providers,
            agent_validator,
            AgentFactory(self.tools, self.skills),
            capability_authorizer,
        )
        workflow_validator = WorkflowValidator(
            lambda: self.tools.names,
            self.providers.names,
            lambda: self.skills.names,
        )
        graphs = WorkflowGraphFactory(
            agent_runner=agent_runner,
            capabilities=self.tools,
            capability_authorizer=capability_authorizer,
        )
        self.runner = WorkflowVersionRunner(workflow_validator, self.tools, graphs)
        self.telemetry = Telemetry(settings)
        self.artifact: WorkflowArtifact | None = None

    async def initialize(self) -> None:
        artifact = load_workflow_artifact(self.settings.workflow_artifact_dir)
        version = self._snapshot(artifact)
        self.tools.verify_contracts(version.capability_contracts)
        self.runner.validator.validate(version.spec)
        self.artifact = artifact
        audit_event(
            "runtime.ready",
            workflow_id=str(artifact.workflow.workflow_id),
            workflow_version_id=str(artifact.workflow.workflow_version_id),
        )

    async def close(self) -> None:
        return None

    def metadata(self) -> RuntimeMetadata:
        artifact = self._artifact()
        return RuntimeMetadata(
            manifest=artifact.manifest,
            runtime_id=self.runtime_identity.id,
            workflow={
                "name": artifact.workflow.spec.name,
                "description": artifact.workflow.spec.description,
                "version": artifact.workflow.version,
                "nodes": [node.id for node in artifact.workflow.spec.nodes],
                "capabilities": [
                    contract.capability_id for contract in artifact.workflow.capability_contracts
                ],
            },
            observability={
                "mlflow": self.telemetry.enabled,
                "langchain_autolog": self.telemetry.langchain_autolog_enabled,
            },
        )

    async def invoke(self, workflow_input: dict[str, Any]) -> InvokeResponse:
        if not isinstance(workflow_input, dict):
            raise ValueError("workflow input must be a JSON object")
        artifact = self._artifact()
        run_id = str(uuid4())
        trace_id = str(uuid4())
        mlflow_trace_id = None
        with (
            trace_context(trace_id),
            self.telemetry.span(
                "deployed_workflow.run",
                {
                    "app.trace_id": trace_id,
                    "run.id": run_id,
                    "workflow.id": str(artifact.workflow.workflow_id),
                    "workflow.version.id": str(artifact.workflow.workflow_version_id),
                    "workflow.version": artifact.workflow.version,
                },
            ) as span,
        ):
            mlflow_trace_id = self.telemetry.trace_id(span)
            self.telemetry.set_inputs(span, workflow_input)
            result = await self.runner.run(
                self._snapshot(artifact),
                workflow_input,
                ExecutionPolicy(
                    timeout_seconds=self.settings.workflow_timeout_seconds,
                    recursion_limit=self.settings.workflow_recursion_limit,
                ),
                execution_id=run_id,
            )
            self.telemetry.set_outputs(span, {"output": result.output})
        return InvokeResponse(
            run_id=run_id,
            trace_id=trace_id,
            mlflow_trace_id=mlflow_trace_id,
            output=result.output,
        )

    def _artifact(self) -> WorkflowArtifact:
        if self.artifact is None:
            raise RuntimeError("standalone workflow runtime is not initialized")
        return self.artifact

    @staticmethod
    def _snapshot(artifact: WorkflowArtifact) -> WorkflowVersionSnapshot:
        return WorkflowVersionSnapshot(
            id=artifact.workflow.workflow_version_id,
            workflow_id=artifact.workflow.workflow_id,
            version=artifact.workflow.version,
            spec=artifact.workflow.spec,
            capability_contracts=artifact.workflow.capability_contracts,
        )
