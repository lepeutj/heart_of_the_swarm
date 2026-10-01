import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for

from heart_of_the_swarm.agent_runtime import AgentRunner
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
    TransformNodeConfig,
    VersionedAgentSource,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.agent_versions import AgentVersionResolver
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
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


@dataclass(frozen=True)
class NodeExecution:
    """Result of adapting and executing one validated workflow node."""

    state: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str | None = None
    output: Any = None
    is_output: bool = False


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
    ) -> None:
        self.workflow = workflow
        self.callback = callback
        self.workflow_run_id = workflow_run_id
        self.agent_runner = agent_runner
        self.agent_versions = agent_versions
        self.capabilities = capabilities

    async def run(
        self,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
        state: WorkflowState,
        executed_nodes: tuple[str, ...],
    ) -> NodeExecution:
        """Execute one node with normalized errors and trajectory events."""
        if node.id in executed_nodes:
            self._raise(
                code="workflow.execution.repeated_node",
                message=f"Node '{node.id}' was selected more than once.",
                node=node,
            )

        current_execution = (*executed_nodes, node.id)
        self._record_node("node.started", node)
        try:
            if isinstance(node.config, InputNodeConfig):
                self._validate_json(node, state, self.workflow.input_schema, "input")
                result = NodeExecution(state, current_execution)
            elif isinstance(node.config, TransformNodeConfig):
                result = NodeExecution(self._apply_transform(node, state), current_execution)
            elif isinstance(node.config, AgentNodeConfig):
                result = NodeExecution(
                    await self._invoke_agent(node, state),
                    current_execution,
                )
            elif isinstance(node.config, ConnectorNodeConfig):
                result = NodeExecution(
                    await self._invoke_connector(node, state),
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
        except WorkflowExecutionError as exc:
            self._record_node(
                "node.failed",
                node,
                {
                    "error_code": exc.issue.code,
                    "safe_message": exc.issue.message,
                },
            )
            raise

        self._record_node("node.completed", node)
        return result

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
    ) -> WorkflowState:
        """Resolve one inline or saved agent and delegate its loop to AgentRunner."""
        config = node.config
        if not isinstance(config, AgentNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent agent configuration.")
        if self.agent_runner is None:
            self._raise(
                code="workflow.execution.agent_runtime_unavailable",
                message="Agent execution is not configured for this workflow runtime.",
                node=node,
            )

        agent_input = self._resolve_agent_input(node, config, state)

        if isinstance(config.source, InlineAgentSource):
            spec = config.source.agent
            system_prompt = None
        else:
            source = config.source
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

        try:
            output = await self.agent_runner.invoke(
                spec,
                agent_input,
                system_prompt=system_prompt,
                callbacks=[self.callback] if self.callback is not None else [],
                metadata=self._node_context(node),
                response_schema=config.response_schema,
            )
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            self._raise(
                code="workflow.execution.agent_failed",
                message=f"Agent node '{node.id}' failed.",
                node=node,
                cause=exc,
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

    def _resolve_agent_input(
        self,
        node: ValidatedWorkflowNode,
        config: AgentNodeConfig,
        state: WorkflowState,
    ) -> str:
        """Resolve one legacy input or serialize named state inputs for the agent."""
        try:
            if config.input_path is not None:
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
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.agent_input_missing",
                message=f"Input for agent node '{node.id}' could not be resolved.",
                node=node,
                cause=exc,
            )

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
            capability = self.capabilities.resolve_one(config.capability_id)
            if capability.handle_tool_error or capability.handle_validation_error:
                raise ValueError(
                    "connector capabilities must propagate validation and execution errors"
                )
            result = await capability.ainvoke(
                arguments,
                config={
                    "callbacks": [self.callback] if self.callback is not None else [],
                    "metadata": self._node_context(node),
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

    def _node_context(self, node: ValidatedWorkflowNode) -> dict[str, Any]:
        """Build metadata shared by workflow-node and nested agent events."""
        return {
            "workflow_id": str(self.workflow.id),
            "workflow_run_id": self.workflow_run_id,
            "node_id": node.id,
            "node_type": str(node.type),
        }

    def _record_node(
        self,
        event_type: str,
        node: ValidatedWorkflowNode,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record node lifecycle through the existing trajectory callback."""
        if self.callback is None:
            return
        self.callback.record(
            event_type,
            component=node.id,
            payload={**self._node_context(node), **(payload or {})},
        )

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
