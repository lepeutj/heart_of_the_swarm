import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from pydantic import BaseModel

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.authorization import AuthorizationContext
from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.conditions import ConditionEvaluationError, evaluate_condition
from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConditionNodeConfig,
    ConnectorNodeConfig,
    InlineAgentSource,
    InputNodeConfig,
    OutputNodeConfig,
    SupervisorNodeConfig,
    TransformNodeConfig,
    VersionedAgentSource,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.agent_versions import AgentVersionResolver
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.execution.validation import validate_workflow_input
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
)
from heart_of_the_swarm.workflows.state import (
    StatePathError,
    WorkflowState,
    get_path,
    resolve_value,
    set_path,
)
from heart_of_the_swarm.workflows.supervisors import SupervisorDecision


@dataclass(frozen=True)
class NodeExecution:
    """Result of adapting and executing one validated workflow node."""

    state: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str | None = None
    output: Any = None
    is_output: bool = False
    supervisor_decision: SupervisorDecision | None = None


class WorkflowNodeRunner:
    """Provide product-specific node operations for the LangGraph adapter."""

    def __init__(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        agent_runner: AgentRunner | None = None,
        agent_versions: AgentVersionResolver | None = None,
        capabilities: ToolRegistry | None = None,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
        event_sink: Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]]
        | None = None,
        workflow_version_id: str | None = None,
        execution_path: str = "root",
        runtime_context: dict[str, Any] | None = None,
        capability_authorizer: CapabilityAuthorizer | None = None,
    ) -> None:
        self.workflow = workflow
        self.callback = callback
        self.workflow_run_id = workflow_run_id
        self.event_sink = event_sink
        self.agent_runner = agent_runner
        self.agent_versions = agent_versions
        self.capabilities = capabilities
        self.workflow_version_id = workflow_version_id
        self.execution_path = execution_path
        self.runtime_context = dict(runtime_context or {})
        self.capability_authorizer = capability_authorizer

    async def run(
        self,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
        state: WorkflowState,
        executed_nodes: tuple[str, ...],
        event_context: dict[str, Any] | None = None,
        agent_input_overrides: dict[str, Any] | None = None,
    ) -> NodeExecution:
        """Execute one node with normalized errors and trajectory events."""
        current_execution = (*executed_nodes, node.id)
        await self._record_node("node.started", node, context=event_context)
        try:
            if isinstance(node.config, InputNodeConfig):
                validate_workflow_input(self.workflow, state, node)
                result = NodeExecution(state, current_execution)
            elif isinstance(node.config, TransformNodeConfig):
                result = NodeExecution(self._apply_transform(node, state), current_execution)
            elif isinstance(node.config, AgentNodeConfig):
                result = NodeExecution(
                    await self._invoke_agent(
                        node,
                        state,
                        event_context,
                        agent_input_overrides,
                    ),
                    current_execution,
                )
            elif isinstance(node.config, SupervisorNodeConfig):
                decision = await self._invoke_supervisor(node, state, event_context)
                result = NodeExecution(
                    state,
                    current_execution,
                    supervisor_decision=decision,
                )
            elif isinstance(node.config, ConnectorNodeConfig):
                result = NodeExecution(
                    await self._invoke_connector(node, state, event_context),
                    current_execution,
                )
            elif isinstance(node.config, ConditionNodeConfig):
                result = NodeExecution(
                    state,
                    current_execution,
                    selected_target=self._select_condition_target(node, outgoing_edges, state),
                )
            elif isinstance(node.config, OutputNodeConfig):
                output = self._resolve_output(node, state)
                if self.workflow.output_schema is not None:
                    self._validate_json(node, output, self.workflow.output_schema, "output")
                result = NodeExecution(
                    state,
                    current_execution,
                    output=deepcopy(output),
                    is_output=True,
                )
            else:
                self._raise(
                    code="workflow.execution.unsupported_node",
                    message=f"Node type '{node.type}' is not supported by this runtime.",
                    node=node,
                )
        except asyncio.CancelledError:
            await self._record_node(
                "node.failed",
                node,
                {
                    "error_code": "workflow.execution.cancelled",
                    "safe_message": f"Node '{node.id}' execution was cancelled.",
                },
                context=event_context,
            )
            raise
        except WorkflowExecutionError as exc:
            await self._record_node(
                "node.failed",
                node,
                {
                    "error_code": exc.issue.code,
                    "safe_message": exc.issue.message,
                },
                context=event_context,
            )
            raise

        await self._record_node("node.completed", node, context=event_context)
        return result

    async def record_started(
        self,
        node: ValidatedWorkflowNode,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record the start of a framework-managed composite node."""
        await self._record_node("node.started", node, context=context)

    async def record_completed(
        self,
        node: ValidatedWorkflowNode,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record the completion of a framework-managed composite node."""
        await self._record_node("node.completed", node, context=context)

    async def record_failed(
        self,
        node: ValidatedWorkflowNode,
        *,
        error_code: str,
        safe_message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a normalized failure for a framework-managed composite node."""
        await self._record_node(
            "node.failed",
            node,
            {"error_code": error_code, "safe_message": safe_message},
            context=context,
        )

    async def record_event(
        self,
        event_type: str,
        node: ValidatedWorkflowNode,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit one contextual workflow event outside normal node lifecycle events."""
        await self._record_node(event_type, node, payload, context=context)

    def reject_non_object_input(self, node: ValidatedWorkflowNode) -> NoReturn:
        """Raise the stable workflow error used for non-object input."""
        self._raise(
            code="workflow.execution.input_not_object",
            message="Workflow input must be an object.",
            node=node,
        )

    async def _invoke_agent(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
        event_context: dict[str, Any] | None = None,
        input_overrides: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Resolve one inline or saved agent and delegate its loop to AgentRunner."""
        config = node.config
        if not isinstance(config, AgentNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent agent configuration.")
        agent_input = self._resolve_agent_input(node, config, state, input_overrides)

        output = await self._call_agent(
            node,
            config.source,
            agent_input,
            config.response_schema,
            event_context,
        )

        try:
            updated = self._write_agent_output(node, config, state, output)
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            self._raise(
                code="workflow.execution.agent_output_failed",
                message=f"Output from agent node '{node.id}' could not be written to state.",
                node=node,
                cause=exc,
            )
        return updated

    async def _invoke_supervisor(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
        event_context: dict[str, Any] | None = None,
    ) -> SupervisorDecision:
        """Invoke one supervisor agent and validate its closed routing decision."""
        config = self._require_config(node, SupervisorNodeConfig)
        agent_input = self._serialize_named_inputs(node, config.inputs, state)
        output = await self._call_agent(
            node,
            config.source,
            agent_input,
            SupervisorDecision,
            event_context,
        )
        try:
            decision = SupervisorDecision.model_validate(output)
        except Exception as exc:
            self._raise(
                code="workflow.execution.supervisor_decision_invalid",
                message=f"Supervisor node '{node.id}' returned an invalid routing decision.",
                node=node,
                cause=exc,
            )
        if decision.root.action == "handoff" and decision.root.target not in config.allowed_targets:
            self._raise(
                code="workflow.execution.supervisor_target_not_allowed",
                message=(
                    f"Supervisor node '{node.id}' selected an unavailable target "
                    f"'{decision.root.target}'."
                ),
                node=node,
            )
        payload: dict[str, Any] = {"action": decision.root.action}
        if decision.root.action == "handoff":
            payload["target_node_id"] = decision.root.target
        await self._record_node("supervisor.decision", node, payload, context=event_context)
        return decision

    async def _call_agent(
        self,
        node: ValidatedWorkflowNode,
        source: InlineAgentSource | VersionedAgentSource,
        agent_input: str,
        response_schema: dict[str, Any] | type[BaseModel] | None,
        event_context: dict[str, Any] | None,
    ) -> Any:
        """Resolve one agent source and delegate exactly one invocation to AgentRunner."""
        if self.agent_runner is None:
            self._raise(
                code="workflow.execution.agent_runtime_unavailable",
                message="Agent execution is not configured for this workflow runtime.",
                node=node,
            )

        if isinstance(source, InlineAgentSource):
            spec = source.agent
            system_prompt = None
            security = self._workflow_security(node)
        else:
            if not isinstance(source, VersionedAgentSource):
                raise TypeError(f"Node '{node.id}' has an inconsistent agent source.")
            if self.agent_versions is None:
                self._raise(
                    code="workflow.execution.agent_version_resolver_unavailable",
                    message="Saved agent versions cannot be resolved by this workflow runtime.",
                    node=node,
                )
            resolved = await self.agent_versions.resolve(source.agent_version_id)
            if resolved is None:
                self._raise(
                    code="workflow.execution.agent_version_not_found",
                    message=f"Agent version '{source.agent_version_id}' was not found.",
                    node=node,
                )
            spec = resolved.spec
            system_prompt = resolved.system_prompt
            security = self._agent_security(node, str(source.agent_version_id))

        try:
            invocation_options: dict[str, Any] = {
                "system_prompt": system_prompt,
                "callbacks": [self.callback] if self.callback is not None else [],
                "metadata": self._node_context(node, event_context),
                "response_schema": response_schema,
            }
            if security is not None:
                invocation_options["security"] = security
            output = await self.agent_runner.invoke(spec, agent_input, **invocation_options)
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            self._raise(
                code="workflow.execution.agent_failed",
                message=f"Agent node '{node.id}' failed.",
                node=node,
                cause=exc,
            )
        return output

    def _resolve_agent_input(
        self,
        node: ValidatedWorkflowNode,
        config: AgentNodeConfig,
        state: WorkflowState,
        input_overrides: dict[str, Any] | None = None,
    ) -> str:
        """Resolve one legacy input or serialize named state inputs for the agent."""
        try:
            if config.input_path is not None:
                if input_overrides:
                    raise ValueError("scalar agent input cannot receive delegated fields")
                value = get_path(state, config.input_path)
                if not isinstance(value, str):
                    self._raise(
                        code="workflow.execution.agent_input_invalid",
                        message=f"Input for agent node '{node.id}' must be a string.",
                        node=node,
                    )
                return value

            inputs = {
                name: get_path(state, binding.from_state)
                for name, binding in (config.inputs or {}).items()
            }
            inputs.update(deepcopy(input_overrides or {}))
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.agent_input_missing",
                message=f"Input for agent node '{node.id}' could not be resolved.",
                node=node,
                cause=exc,
            )

        return self._serialize_inputs(node, inputs)

    def _serialize_named_inputs(
        self,
        node: ValidatedWorkflowNode,
        bindings: Mapping[str, Any],
        state: WorkflowState,
    ) -> str:
        """Resolve declared named inputs and serialize them for one agent invocation."""
        try:
            inputs = {
                name: get_path(state, binding.from_state) for name, binding in bindings.items()
            }
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.agent_input_missing",
                message=f"Input for agent node '{node.id}' could not be resolved.",
                node=node,
                cause=exc,
            )
        return self._serialize_inputs(node, inputs)

    def _serialize_inputs(self, node: ValidatedWorkflowNode, inputs: dict[str, Any]) -> str:
        """Serialize named agent inputs consistently across agents and supervisors."""
        if len(inputs) == 1:
            value = next(iter(inputs.values()))
            if isinstance(value, str):
                return value
        try:
            return json.dumps(inputs, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            self._raise(
                code="workflow.execution.agent_input_invalid",
                message=f"Inputs for agent node '{node.id}' must be JSON serializable.",
                node=node,
                cause=exc,
            )

    def _write_agent_output(
        self,
        node: ValidatedWorkflowNode,
        config: AgentNodeConfig,
        state: WorkflowState,
        output: Any,
    ) -> WorkflowState:
        """Atomically project one scalar or named structured response into state."""
        updated = deepcopy(state)
        if config.output_path is not None:
            set_path(updated, config.output_path, output)
            return updated

        bindings = config.outputs or {}
        if config.response_schema is None:
            binding = next(iter(bindings.values()))
            set_path(updated, binding.to_state, output)
            return updated

        self._validate_json(node, output, config.response_schema, "agent_output")
        if not isinstance(output, Mapping):
            raise TypeError("structured agent output must be an object")
        missing = [name for name in bindings if name not in output]
        if missing:
            raise ValueError(f"structured agent output is missing fields: {', '.join(missing)}")
        values = {name: deepcopy(output[name]) for name in bindings}
        for name, binding in bindings.items():
            set_path(updated, binding.to_state, values[name])
        return updated

    async def _invoke_connector(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
        event_context: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Invoke one registered capability and project its result into state."""
        config = self._require_config(node, ConnectorNodeConfig)
        if self.capabilities is None:
            self._raise(
                code="workflow.execution.capability_registry_unavailable",
                message="Connector execution is not configured for this workflow runtime.",
                node=node,
            )
        try:
            arguments = {name: resolve_value(value, state) for name, value in config.inputs.items()}
            if self.capability_authorizer is not None:
                security = self._workflow_security(node)
                await self.capability_authorizer.require_invocation(
                    security.principal,
                    config.capability_id,
                    context=security.authorization,
                )
            result = await self.capabilities.invoke_one(
                config.capability_id,
                arguments,
                config={
                    "callbacks": [self.callback] if self.callback is not None else [],
                    "metadata": self._node_context(node, event_context),
                },
            )
            if isinstance(result, Mapping):
                missing = [name for name in config.outputs if name not in result]
                if missing:
                    raise ValueError(f"connector result is missing fields: {', '.join(missing)}")
                values = {name: result[name] for name in config.outputs}
            elif len(config.outputs) == 1:
                values = {next(iter(config.outputs)): result}
            else:
                raise TypeError("multiple connector outputs require an object result")
            updated = deepcopy(state)
            for name, binding in config.outputs.items():
                set_path(updated, binding.to_state, deepcopy(values[name]))
            return updated
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            self._raise(
                code="workflow.execution.connector_failed",
                message=(
                    f"Connector node '{node.id}' failed while invoking '{config.capability_id}'."
                ),
                node=node,
                cause=exc,
            )

    def _workflow_security(self, node: ValidatedWorkflowNode) -> ExecutionSecurityContext | None:
        """Build the immutable workflow-node identity when runtime authorization is enabled."""
        if self.capability_authorizer is None:
            return None
        if self.workflow_version_id is None:
            raise RuntimeError("capability authorization requires a workflow version id")
        return ExecutionSecurityContext(
            principal=self.capability_authorizer.workflow_node_principal(
                self.workflow_version_id,
                node.id,
            ),
            authorization=self._authorization_context(node),
        )

    def _agent_security(
        self,
        node: ValidatedWorkflowNode,
        agent_version_id: str,
    ) -> ExecutionSecurityContext | None:
        """Build a saved-agent identity while retaining its invoking workflow as context."""
        if self.capability_authorizer is None:
            return None
        return ExecutionSecurityContext(
            principal=self.capability_authorizer.agent_version_principal(agent_version_id),
            authorization=self._authorization_context(node),
        )

    def _authorization_context(self, node: ValidatedWorkflowNode) -> AuthorizationContext:
        """Project trusted workflow execution identifiers into authorization metadata."""
        return AuthorizationContext(
            workflow_version_id=self.workflow_version_id,
            node_id=node.id,
            run_id=self.workflow_run_id,
        )

    def _apply_transform(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
    ) -> WorkflowState:
        """Resolve all declarative assignments before applying them to copied state."""
        config = self._require_config(node, TransformNodeConfig)
        source = deepcopy(state)
        updated = deepcopy(state)
        try:
            resolved = {path: resolve_value(value, source) for path, value in config.assign.items()}
            for path, value in resolved.items():
                set_path(updated, path, value)
        except (StatePathError, ValueError) as exc:
            self._raise(
                code="workflow.execution.transform_failed",
                message=f"Transform node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )
        return updated

    def _select_condition_target(
        self,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
        state: WorkflowState,
    ) -> str:
        """Return the first matching declared edge or the validated fallback."""
        try:
            for edge in outgoing_edges:
                if edge.condition is not None and evaluate_condition(edge.condition, state):
                    return edge.target
        except ConditionEvaluationError as exc:
            self._raise(
                code="workflow.execution.condition_failed",
                message=f"Condition node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )
        return next(edge.target for edge in outgoing_edges if edge.condition is None)

    def _resolve_output(self, node: ValidatedWorkflowNode, state: WorkflowState) -> Any:
        """Resolve one legacy path or a structured set of workflow outputs."""
        config = self._require_config(node, OutputNodeConfig)
        try:
            if config.output_path is not None:
                return get_path(state, config.output_path)
            return {
                name: deepcopy(get_path(state, binding.from_state))
                for name, binding in (config.outputs or {}).items()
            }
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.output_missing",
                message=f"Output node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )

    def _validate_json(
        self,
        node: ValidatedWorkflowNode,
        value: Any,
        schema: dict[str, Any],
        boundary: str,
    ) -> None:
        """Translate JSON Schema validation failures into workflow errors."""
        try:
            validator_for(schema)(schema).validate(value)
        except JsonSchemaValidationError as exc:
            self._raise(
                code=f"workflow.execution.invalid_{boundary}",
                message=f"Workflow {boundary} is invalid: {exc.message}",
                node=node,
                cause=exc,
            )

    def _node_context(
        self,
        node: ValidatedWorkflowNode,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build metadata shared by workflow-node and nested agent events."""
        return {
            "workflow_id": str(self.workflow.id),
            "workflow_version_id": self.workflow_version_id,
            "workflow_run_id": self.workflow_run_id,
            "node_id": node.id,
            "node_type": str(node.type),
            "execution_path": f"{self.execution_path}/{node.id}",
            **self.runtime_context,
            **(extra or {}),
        }

    async def _record_node(
        self,
        event_type: str,
        node: ValidatedWorkflowNode,
        payload: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record node lifecycle through the existing trajectory callback."""
        event_payload = {**self._node_context(node, context), **(payload or {})}
        if self.callback is not None:
            self.callback.record(
                event_type,
                component=node.id,
                payload=event_payload,
            )
        if self.event_sink is not None:
            await self.event_sink(event_type, node, event_payload)

    def _raise(
        self,
        code: str,
        message: str,
        node: ValidatedWorkflowNode,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise one normalized error with workflow and node context."""
        error = WorkflowExecutionError(
            WorkflowExecutionIssue(
                code=code,
                message=message,
                workflow_id=self.workflow.id,
                node_id=node.id,
                node_type=NodeType(node.type),
            )
        )
        if cause is None:
            raise error
        raise error from cause

    @staticmethod
    def _require_config(node: ValidatedWorkflowNode, expected_type: type[Any]) -> Any:
        """Return the validated typed config or reject an inconsistent runtime object."""
        if isinstance(node.config, expected_type):
            return node.config
        raise TypeError(f"Node '{node.id}' has an inconsistent validated configuration.")
