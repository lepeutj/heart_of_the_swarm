import asyncio
from copy import deepcopy
from typing import Any, NoReturn

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from langchain_core.tools import BaseTool
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1

from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.compilation import (
    CompiledConditionNode,
    CompiledInputNode,
    CompiledNode,
    CompiledOutputNode,
    CompiledToolNode,
    CompiledTransformNode,
    ExecutionPlan,
)
from heart_of_the_swarm.workflows.conditions import (
    ConditionEvaluationError,
    evaluate_condition,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.execution.models import ExecutionResult
from heart_of_the_swarm.workflows.state import (
    StatePathError,
    WorkflowState,
    get_path,
    resolve_arguments,
    resolve_value,
    set_path,
)


class WorkflowExecutor:
    """Execute compiled workflows with injected, allow-listed runtime capabilities."""

    def __init__(self, tools: ToolRegistry | None = None) -> None:
        self.tools = tools or ToolRegistry([])

    def execute(
        self,
        plan: ExecutionPlan,
        workflow_input: WorkflowState,
        *,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
    ) -> ExecutionResult:
        """Synchronously execute a plan when no event loop is already running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.aexecute(
                    plan,
                    workflow_input,
                    callback=callback,
                    workflow_run_id=workflow_run_id,
                )
            )
        raise RuntimeError("use WorkflowExecutor.aexecute inside an active event loop")

    async def aexecute(
        self,
        plan: ExecutionPlan,
        workflow_input: WorkflowState,
        *,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
    ) -> ExecutionResult:
        """Execute one selected path, invoking each encountered tool node exactly once."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("WorkflowExecutor requires an ExecutionPlan.")
        if not isinstance(workflow_input, dict):
            self._raise(
                plan,
                code="workflow.execution.input_not_object",
                message="Workflow input must be an object.",
                node=self._entrypoint_node(plan),
            )

        resolved_tools = self._resolve_plan_tools(plan)
        nodes = {node.id: node for node in plan.nodes}
        state = deepcopy(workflow_input)
        executed: list[str] = []
        current_id = plan.entrypoint

        while True:
            node = nodes[current_id]
            if node.id in executed:
                self._raise(
                    plan,
                    code="workflow.execution.repeated_node",
                    message=f"Node '{node.id}' was selected more than once.",
                    node=node,
                )
            executed.append(node.id)
            self._record_node(callback, "node.started", plan, node, workflow_run_id)

            try:
                if isinstance(node, CompiledInputNode):
                    self._validate_json(plan, node, state, plan.input_schema, "input")
                    current_id = node.next_node
                elif isinstance(node, CompiledTransformNode):
                    state = self._apply_transform(plan, node, state)
                    current_id = node.next_node
                elif isinstance(node, CompiledToolNode):
                    state = await self._invoke_tool(
                        plan,
                        node,
                        state,
                        resolved_tools[node.id],
                        callback,
                        workflow_run_id,
                    )
                    current_id = node.next_node
                elif isinstance(node, CompiledConditionNode):
                    current_id = self._select_condition_target(plan, node, state)
                elif isinstance(node, CompiledOutputNode):
                    output = self._resolve_output(plan, node, state)
                    self._validate_json(plan, node, output, plan.output_schema, "output")
                    self._record_node(callback, "node.completed", plan, node, workflow_run_id)
                    return ExecutionResult(
                        workflow_id=plan.workflow_id,
                        output=deepcopy(output),
                        state=deepcopy(state),
                        executed_nodes=tuple(executed),
                    )
                else:
                    self._raise(
                        plan,
                        code="workflow.execution.unsupported_node",
                        message=f"Node type '{node.type}' is not supported by this executor.",
                        node=node,
                    )
            except WorkflowExecutionError as exc:
                self._record_node(
                    callback,
                    "node.failed",
                    plan,
                    node,
                    workflow_run_id,
                    {
                        "error_code": exc.issue.code,
                        "safe_message": exc.issue.message,
                        "tool_name": exc.issue.tool_name,
                    },
                )
                raise

            self._record_node(callback, "node.completed", plan, node, workflow_run_id)

    def _resolve_plan_tools(self, plan: ExecutionPlan) -> dict[str, BaseTool]:
        """Resolve every compiled tool capability before workflow execution starts."""
        resolved: dict[str, BaseTool] = {}
        for node in plan.nodes:
            if not isinstance(node, CompiledToolNode):
                continue
            try:
                tool = self.tools.resolve_one(node.config.tool)
            except ValueError as exc:
                self._raise(
                    plan,
                    code="workflow.execution.tool_unknown",
                    message=f"Tool '{node.config.tool}' is not registered.",
                    node=node,
                    tool_name=node.config.tool,
                    cause=exc,
                )
            if tool.handle_validation_error or tool.handle_tool_error:
                self._raise(
                    plan,
                    code="workflow.execution.tool_error_policy_unsupported",
                    message=(
                        f"Tool '{node.config.tool}' uses an unsupported error-handling policy."
                    ),
                    node=node,
                    tool_name=node.config.tool,
                )
            resolved[node.id] = tool
        return resolved

    async def _invoke_tool(
        self,
        plan: ExecutionPlan,
        node: CompiledToolNode,
        state: WorkflowState,
        tool: BaseTool,
        callback: RuntimeCallbackHandler | None,
        workflow_run_id: str | None,
    ) -> WorkflowState:
        """Resolve arguments, invoke one tool once, and write its result to copied state."""
        try:
            arguments = resolve_arguments(node.config.arguments, state)
        except (StatePathError, ValueError) as exc:
            self._raise(
                plan,
                code="workflow.execution.tool_arguments_unresolved",
                message=f"Arguments for tool '{node.config.tool}' could not be resolved.",
                node=node,
                tool_name=node.config.tool,
                cause=exc,
            )

        metadata = self._node_context(plan, node, workflow_run_id)
        try:
            result = await tool.ainvoke(
                arguments,
                config={
                    "callbacks": [callback] if callback is not None else [],
                    "metadata": metadata,
                },
            )
        except (ValidationError, ValidationErrorV1) as exc:
            self._raise(
                plan,
                code="workflow.execution.tool_arguments_invalid",
                message=f"Arguments for tool '{node.config.tool}' are invalid.",
                node=node,
                tool_name=node.config.tool,
                cause=exc,
            )
        except Exception as exc:
            self._raise(
                plan,
                code="workflow.execution.tool_failed",
                message=f"Tool '{node.config.tool}' failed.",
                node=node,
                tool_name=node.config.tool,
                cause=exc,
            )

        try:
            updated = deepcopy(state)
            set_path(updated, node.config.output_path, deepcopy(result))
        except Exception as exc:
            self._raise(
                plan,
                code="workflow.execution.tool_output_failed",
                message=f"Result from tool '{node.config.tool}' could not be written to state.",
                node=node,
                tool_name=node.config.tool,
                cause=exc,
            )
        return updated

    def _apply_transform(
        self,
        plan: ExecutionPlan,
        node: CompiledTransformNode,
        state: WorkflowState,
    ) -> WorkflowState:
        """Resolve all assignment values from one snapshot, then apply them to a copied state."""
        source = deepcopy(state)
        updated = deepcopy(state)
        try:
            resolved = {
                path: resolve_value(value, source) for path, value in node.config.assign.items()
            }
            for path, value in resolved.items():
                set_path(updated, path, value)
        except (StatePathError, ValueError) as exc:
            self._raise(
                plan,
                code="workflow.execution.transform_failed",
                message=f"Transform node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )
        return updated

    def _select_condition_target(
        self,
        plan: ExecutionPlan,
        node: CompiledConditionNode,
        state: WorkflowState,
    ) -> str:
        """Return the first matching route target, or the compiled fallback target."""
        try:
            for route in node.routes:
                if evaluate_condition(route.condition, state):
                    return route.target
        except ConditionEvaluationError as exc:
            self._raise(
                plan,
                code="workflow.execution.condition_failed",
                message=f"Condition node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )
        return node.fallback

    def _resolve_output(
        self,
        plan: ExecutionPlan,
        node: CompiledOutputNode,
        state: WorkflowState,
    ) -> Any:
        """Resolve an output node's configured state path with node-specific error context."""
        try:
            return get_path(state, node.config.output_path)
        except StatePathError as exc:
            self._raise(
                plan,
                code="workflow.execution.output_missing",
                message=f"Output node '{node.id}' failed: {exc}",
                node=node,
                cause=exc,
            )

    def _validate_json(
        self,
        plan: ExecutionPlan,
        node: CompiledNode,
        value: Any,
        schema: dict[str, Any],
        boundary: str,
    ) -> None:
        """Validate an input or output value and translate JSON Schema errors to domain errors."""
        try:
            validator_for(schema)(schema).validate(value)
        except JsonSchemaValidationError as exc:
            self._raise(
                plan,
                code=f"workflow.execution.invalid_{boundary}",
                message=f"Workflow {boundary} is invalid: {exc.message}",
                node=node,
                cause=exc,
            )

    @staticmethod
    def _entrypoint_node(plan: ExecutionPlan) -> CompiledNode:
        """Return the compiled entrypoint node from a valid execution plan."""
        return next(node for node in plan.nodes if node.id == plan.entrypoint)

    @staticmethod
    def _node_context(
        plan: ExecutionPlan,
        node: CompiledNode,
        workflow_run_id: str | None,
    ) -> dict[str, Any]:
        """Build serializable workflow context shared by node and tool trajectory events."""
        return {
            "workflow_id": str(plan.workflow_id),
            "workflow_run_id": workflow_run_id,
            "node_id": node.id,
            "node_type": str(node.type),
        }

    @classmethod
    def _record_node(
        cls,
        callback: RuntimeCallbackHandler | None,
        event_type: str,
        plan: ExecutionPlan,
        node: CompiledNode,
        workflow_run_id: str | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record node lifecycle through the existing runtime trajectory callback."""
        if callback is None:
            return
        callback.record(
            event_type,
            component=node.id,
            payload={**cls._node_context(plan, node, workflow_run_id), **(payload or {})},
        )

    @staticmethod
    def _raise(
        plan: ExecutionPlan,
        code: str,
        message: str,
        node: CompiledNode,
        tool_name: str | None = None,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise one normalized execution error with workflow, node, and optional tool context."""
        error = WorkflowExecutionError(
            WorkflowExecutionIssue(
                code=code,
                message=message,
                workflow_id=plan.workflow_id,
                node_id=node.id,
                node_type=NodeType(node.type),
                tool_name=tool_name,
            )
        )
        if cause is None:
            raise error
        raise error from cause
