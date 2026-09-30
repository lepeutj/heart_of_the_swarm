from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.conditions import ConditionEvaluationError, evaluate_condition
from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConditionNodeConfig,
    InlineAgentSource,
    InputNodeConfig,
    LLMNodeConfig,
    OutputNodeConfig,
    SavedAgentSource,
    ToolNodeConfig,
    TransformNodeConfig,
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
    resolve_arguments,
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
        tools: ToolRegistry,
        *,
        agent_runner: AgentRunner | None = None,
        agent_versions: AgentVersionResolver | None = None,
        providers: ProviderRegistry | None = None,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
    ) -> None:
        self.workflow = workflow
        self.callback = callback
        self.workflow_run_id = workflow_run_id
        self.agent_runner = agent_runner
        self.agent_versions = agent_versions
        self.providers = providers
        self.resolved_tools = self._resolve_workflow_tools(tools)

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
            elif isinstance(node.config, ToolNodeConfig):
                result = NodeExecution(
                    await self._invoke_tool(node, state, self.resolved_tools[node.id]),
                    current_execution,
                )
            elif isinstance(node.config, AgentNodeConfig):
                result = NodeExecution(
                    await self._invoke_agent(node, state),
                    current_execution,
                )
            elif isinstance(node.config, LLMNodeConfig):
                result = NodeExecution(
                    await self._invoke_llm(node, state),
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
                    "tool_name": exc.issue.tool_name,
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

    def _resolve_workflow_tools(self, tools: ToolRegistry) -> dict[str, BaseTool]:
        """Resolve every tool before LangGraph execution starts."""
        resolved: dict[str, BaseTool] = {}
        for node in self.workflow.nodes:
            if not isinstance(node.config, ToolNodeConfig):
                continue
            try:
                tool = tools.resolve_one(node.config.tool)
            except ValueError as exc:
                self._raise(
                    code="workflow.execution.tool_unknown",
                    message=f"Tool '{node.config.tool}' is not registered.",
                    node=node,
                    tool_name=node.config.tool,
                    cause=exc,
                )
            if tool.handle_validation_error or tool.handle_tool_error:
                self._raise(
                    code="workflow.execution.tool_error_policy_unsupported",
                    message=f"Tool '{node.config.tool}' uses an unsupported error-handling policy.",
                    node=node,
                    tool_name=node.config.tool,
                )
            resolved[node.id] = tool
        return resolved

    async def _invoke_tool(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
        tool: BaseTool,
    ) -> WorkflowState:
        """Resolve arguments, invoke one LangChain tool, and update copied state."""
        config = self._require_config(node, ToolNodeConfig)
        try:
            arguments = resolve_arguments(config.arguments, state)
        except (StatePathError, ValueError) as exc:
            self._raise(
                code="workflow.execution.tool_arguments_unresolved",
                message=f"Arguments for tool '{config.tool}' could not be resolved.",
                node=node,
                tool_name=config.tool,
                cause=exc,
            )

        try:
            result = await tool.ainvoke(
                arguments,
                config={
                    "callbacks": [self.callback] if self.callback is not None else [],
                    "metadata": self._node_context(node),
                },
            )
        except (ValidationError, ValidationErrorV1) as exc:
            self._raise(
                code="workflow.execution.tool_arguments_invalid",
                message=f"Arguments for tool '{config.tool}' are invalid.",
                node=node,
                tool_name=config.tool,
                cause=exc,
            )
        except Exception as exc:
            self._raise(
                code="workflow.execution.tool_failed",
                message=f"Tool '{config.tool}' failed.",
                node=node,
                tool_name=config.tool,
                cause=exc,
            )

        try:
            updated = deepcopy(state)
            set_path(updated, config.output_path, deepcopy(result))
        except Exception as exc:
            self._raise(
                code="workflow.execution.tool_output_failed",
                message=f"Result from tool '{config.tool}' could not be written to state.",
                node=node,
                tool_name=config.tool,
                cause=exc,
            )
        return updated

    async def _invoke_agent(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
    ) -> WorkflowState:
        """Resolve one inline or saved agent and delegate its loop to AgentRunner."""
        config = self._require_config(node, AgentNodeConfig)
        if self.agent_runner is None:
            self._raise(
                code="workflow.execution.agent_runtime_unavailable",
                message="Agent execution is not configured for this workflow runtime.",
                node=node,
            )

        try:
            agent_input = get_path(state, config.input_path)
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.agent_input_missing",
                message=f"Input for agent node '{node.id}' could not be resolved.",
                node=node,
                cause=exc,
            )
        if not isinstance(agent_input, str):
            self._raise(
                code="workflow.execution.agent_input_invalid",
                message=f"Input for agent node '{node.id}' must be a string.",
                node=node,
            )

        source = config.agent
        if isinstance(source, InlineAgentSource):
            spec = source.spec
            system_prompt = None
        elif isinstance(source, SavedAgentSource):
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
        else:
            raise TypeError(f"Node '{node.id}' has an inconsistent agent source.")

        try:
            output = await self.agent_runner.invoke(
                spec,
                agent_input,
                system_prompt=system_prompt,
                callbacks=[self.callback] if self.callback is not None else [],
                metadata=self._node_context(node),
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
            updated = deepcopy(state)
            set_path(updated, config.output_path, output)
        except Exception as exc:
            self._raise(
                code="workflow.execution.agent_output_failed",
                message=f"Output from agent node '{node.id}' could not be written to state.",
                node=node,
                cause=exc,
            )
        return updated

    async def _invoke_llm(
        self,
        node: ValidatedWorkflowNode,
        state: WorkflowState,
    ) -> WorkflowState:
        """Invoke one configured chat model without tools and write its response to state."""
        config = self._require_config(node, LLMNodeConfig)
        if self.providers is None:
            self._raise(
                code="workflow.execution.llm_runtime_unavailable",
                message="LLM execution is not configured for this workflow runtime.",
                node=node,
            )

        errors = self.providers.validate_model_execution(config.model)
        if errors:
            self._raise(
                code="workflow.execution.llm_model_invalid",
                message="; ".join(errors),
                node=node,
            )

        try:
            model_input = get_path(state, config.input_path)
        except StatePathError as exc:
            self._raise(
                code="workflow.execution.llm_input_missing",
                message=f"Input for LLM node '{node.id}' could not be resolved.",
                node=node,
                cause=exc,
            )
        if not isinstance(model_input, str):
            self._raise(
                code="workflow.execution.llm_input_invalid",
                message=f"Input for LLM node '{node.id}' must be a string.",
                node=node,
            )

        try:
            model = self.providers.create_model(config.model)
            response = await model.ainvoke(
                [SystemMessage(content=config.prompt), HumanMessage(content=model_input)],
                config={
                    "callbacks": [self.callback] if self.callback is not None else [],
                    "metadata": self._node_context(node),
                },
            )
        except Exception as exc:
            self._raise(
                code="workflow.execution.llm_failed",
                message=f"LLM node '{node.id}' failed.",
                node=node,
                cause=exc,
            )
        if not isinstance(response, AIMessage):
            self._raise(
                code="workflow.execution.llm_response_invalid",
                message=f"LLM node '{node.id}' returned an invalid response.",
                node=node,
            )

        output = response.content if isinstance(response.content, str) else str(response.content)
        try:
            updated = deepcopy(state)
            set_path(updated, config.output_path, output)
        except Exception as exc:
            self._raise(
                code="workflow.execution.llm_output_failed",
                message=f"Output from LLM node '{node.id}' could not be written to state.",
                node=node,
                cause=exc,
            )
        return updated

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
        """Resolve a configured output path with node-specific failure context."""
        config = self._require_config(node, OutputNodeConfig)
        try:
            return get_path(state, config.output_path)
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
        """Build metadata shared by node and tool trajectory events."""
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
        tool_name: str | None = None,
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
                tool_name=tool_name,
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
