from copy import deepcopy
from typing import Any, NoReturn

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for

from heart_of_the_swarm.workflows.compilation import (
    CompiledConditionNode,
    CompiledInputNode,
    CompiledNode,
    CompiledOutputNode,
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
    resolve_value,
    set_path,
)


class WorkflowExecutor:
    """Execute compiled deterministic workflows without infrastructure dependencies."""

    def execute(self, plan: ExecutionPlan, workflow_input: WorkflowState) -> ExecutionResult:
        """Run the selected path through a compiled plan and return its output and final state."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("WorkflowExecutor requires an ExecutionPlan.")
        if not isinstance(workflow_input, dict):
            self._raise(
                plan,
                code="workflow.execution.input_not_object",
                message="Workflow input must be an object.",
                node=self._entrypoint_node(plan),
            )

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

            if isinstance(node, CompiledInputNode):
                self._validate_json(plan, node, state, plan.input_schema, "input")
                current_id = node.next_node
                continue
            if isinstance(node, CompiledTransformNode):
                state = self._apply_transform(plan, node, state)
                current_id = node.next_node
                continue
            if isinstance(node, CompiledConditionNode):
                current_id = self._select_condition_target(plan, node, state)
                continue
            if isinstance(node, CompiledOutputNode):
                output = self._resolve_output(plan, node, state)
                self._validate_json(plan, node, output, plan.output_schema, "output")
                return ExecutionResult(
                    workflow_id=plan.workflow_id,
                    output=deepcopy(output),
                    state=deepcopy(state),
                    executed_nodes=tuple(executed),
                )
            self._raise(
                plan,
                code="workflow.execution.unsupported_node",
                message=f"Node type '{node.type}' is not supported by this executor.",
                node=node,
            )

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
    def _raise(
        plan: ExecutionPlan,
        code: str,
        message: str,
        node: CompiledNode,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise one normalized execution error with workflow and node context."""
        error = WorkflowExecutionError(
            WorkflowExecutionIssue(
                code=code,
                message=message,
                workflow_id=plan.workflow_id,
                node_id=node.id,
                node_type=NodeType(node.type),
            )
        )
        if cause is None:
            raise error
        raise error from cause
