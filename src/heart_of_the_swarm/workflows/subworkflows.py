from typing import Any

from heart_of_the_swarm.workflows.configs import SubworkflowNodeConfig
from heart_of_the_swarm.workflows.spec import ValidatedWorkflowSpec

MAX_SUBWORKFLOW_DEPTH = 8


def validate_subworkflow_mappings(
    config: SubworkflowNodeConfig,
    child: ValidatedWorkflowSpec,
) -> None:
    """Validate the declared parent/child boundary without inspecting runtime state."""
    input_properties, required_inputs, allow_extra_inputs = _object_contract(
        child.input_schema,
        "input",
    )
    missing = required_inputs - set(config.inputs)
    if missing:
        raise ValueError(
            f"subworkflow inputs are missing required fields: {', '.join(sorted(missing))}"
        )
    unknown_inputs = set(config.inputs) - input_properties
    if unknown_inputs and not allow_extra_inputs:
        raise ValueError(
            f"subworkflow inputs contain unknown fields: {', '.join(sorted(unknown_inputs))}"
        )

    if child.output_schema is None:
        raise ValueError("subworkflow requires an object output schema")
    output_properties, _, allow_extra_outputs = _object_contract(
        child.output_schema,
        "output",
    )
    unknown_outputs = set(config.outputs) - output_properties
    if unknown_outputs and not allow_extra_outputs:
        raise ValueError(
            f"subworkflow outputs contain unknown fields: {', '.join(sorted(unknown_outputs))}"
        )


def _object_contract(
    schema: dict[str, Any],
    boundary: str,
) -> tuple[set[str], set[str], bool]:
    if schema.get("type") != "object":
        raise ValueError(f"subworkflow {boundary} schema must be an object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ValueError(f"subworkflow {boundary} schema is invalid")
    return (
        set(properties),
        {name for name in required if isinstance(name, str)},
        (schema.get("additionalProperties", True) is not False),
    )
