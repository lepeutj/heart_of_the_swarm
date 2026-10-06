from copy import deepcopy

import pytest

from heart_of_the_swarm.workflows import (
    WorkflowSpec,
    WorkflowValidationError,
    WorkflowValidator,
    workflow_capabilities,
)
from heart_of_the_swarm.workflows.configs import HumanApprovalNodeConfig


def approval_workflow_data() -> dict:
    return {
        "schema_version": "2",
        "id": "94c46455-a7cf-4ce9-ae84-79ec07c0f434",
        "name": "Approval workflow",
        "description": "Pause before publication.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "boolean"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "approve",
                "type": "human_approval",
                "name": "Approve publication",
                "config": {
                    "prompt": "Approve publication of the reviewed report?",
                    "output": {"to_state": "$.publication.approved"},
                },
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.publication.approved"},
            },
        ],
        "edges": [
            {"source": "input", "target": "approve"},
            {"source": "approve", "target": "output"},
        ],
    }


def test_human_approval_contract_is_typed_but_not_runtime_available() -> None:
    workflow = WorkflowValidator([], []).validate(
        WorkflowSpec.model_validate(approval_workflow_data())
    )

    approval = next(node for node in workflow.nodes if node.id == "approve")
    assert isinstance(approval.config, HumanApprovalNodeConfig)
    assert approval.config.output.to_state == "$.publication.approved"

    capability = next(
        item for item in workflow_capabilities().nodes if item.type == "human_approval"
    )
    assert capability.available is False


def test_human_approval_requires_schema_v2() -> None:
    data = approval_workflow_data()
    data["schema_version"] = "1"

    with pytest.raises(WorkflowValidationError) as caught:
        WorkflowValidator([], []).validate(WorkflowSpec.model_validate(data))

    assert "workflow.human_approval.requires_schema_v2" in {
        issue.code for issue in caught.value.issues
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt", ""),
        ("output", {"to_state": "publication.approved"}),
    ],
)
def test_invalid_human_approval_config_is_rejected(field: str, value: object) -> None:
    data = deepcopy(approval_workflow_data())
    data["nodes"][1]["config"][field] = value

    with pytest.raises(WorkflowValidationError) as caught:
        WorkflowValidator([], []).validate(WorkflowSpec.model_validate(data))

    issues = caught.value.issues
    assert any(
        issue.code == "workflow.node.invalid_config"
        and issue.node_id == "approve"
        and issue.field.startswith(f"config.{field}")
        for issue in issues
    )
