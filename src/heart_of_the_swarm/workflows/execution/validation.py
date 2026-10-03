from typing import Any

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for

from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowSpec,
)


def validate_workflow_input(
    workflow: WorkflowSpec | ValidatedWorkflowSpec,
    input_data: Any,
    node: ValidatedWorkflowNode | None = None,
) -> None:
    """Validate invocation data against the workflow's declared input contract."""
    try:
        validator_for(workflow.input_schema)(workflow.input_schema).validate(input_data)
    except JsonSchemaValidationError as exc:
        raise WorkflowExecutionError(
            WorkflowExecutionIssue(
                code="workflow.execution.invalid_input",
                message=f"Workflow input is invalid: {exc.message}",
                workflow_id=workflow.id,
                node_id=node.id if node else None,
                node_type=NodeType(node.type) if node else None,
            )
        ) from exc
