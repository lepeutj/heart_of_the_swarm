from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    FinishDecision,
    HandoffDecision,
    SupervisorDecision,
    WorkflowSpec,
    WorkflowValidationError,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.configs import SupervisorNodeConfig


def agent_source(name: str) -> dict[str, Any]:
    return {
        "type": "inline",
        "agent": {
            "name": name,
            "goal": f"Perform the {name.lower()} role.",
            "instructions": "Complete the delegated task and return a concise result.",
            "model": {"provider": "test", "model_id": "test-model"},
            "tools": [],
            "skills": [],
        },
    }


def agent_node(node_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agent",
        "name": node_id.title(),
        "config": {
            "source": agent_source(node_id.title()),
            "inputs": {"topic": {"from_state": "$.request"}},
            "output_path": f"$.delegates.{node_id}",
        },
    }


def supervisor_workflow_data() -> dict[str, Any]:
    targets = ("researcher", "analyst", "reviewer")
    return {
        "schema_version": "2",
        "id": "5ce5c75a-672a-447e-a92a-d92352f60a88",
        "name": "Supervisor workflow",
        "description": "Route sequential tasks to explicitly allowed agents.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "supervisor",
                "type": "supervisor",
                "name": "Supervisor",
                "config": {
                    "source": agent_source("Supervisor"),
                    "inputs": {"request": {"from_state": "$.request"}},
                    "allowed_targets": {target: {"task_field": "task"} for target in targets},
                    "finish_output": {"to_state": "$.final"},
                },
            },
            *(agent_node(target) for target in targets),
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.final"},
            },
        ],
        "edges": [
            {"source": "input", "target": "supervisor"},
            {"source": "supervisor", "target": "output"},
        ],
    }


def validator() -> WorkflowValidator:
    return WorkflowValidator([], ["test"])


def issues(data: dict[str, Any]) -> list:
    with pytest.raises(WorkflowValidationError) as caught:
        validator().validate(WorkflowSpec.model_validate(data))
    return caught.value.issues


def test_valid_supervisor_with_three_targets_is_typed() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(supervisor_workflow_data()))

    supervisor = next(node for node in workflow.nodes if node.id == "supervisor")
    assert isinstance(supervisor.config, SupervisorNodeConfig)
    assert list(supervisor.config.allowed_targets) == ["researcher", "analyst", "reviewer"]
    assert supervisor.config.max_handoffs_ref == "execution_policy.max_handoffs"
    assert ExecutionPolicy.model_fields["max_handoffs"].default == 20


def test_unknown_supervisor_target_is_rejected() -> None:
    data = supervisor_workflow_data()
    supervisor = data["nodes"][1]
    supervisor["config"]["allowed_targets"] = {"missing": {"task_field": "task"}}

    assert "workflow.supervisor.unknown_target" in {issue.code for issue in issues(data)}


def test_invalid_agent_config_is_not_misreported_as_an_unknown_target() -> None:
    data = supervisor_workflow_data()
    data["nodes"][2]["config"].pop("source")

    codes = {issue.code for issue in issues(data)}
    assert "workflow.node.invalid_config" in codes
    assert "workflow.supervisor.unknown_target" not in codes


def test_subworkflow_supervisor_target_is_rejected() -> None:
    data = supervisor_workflow_data()
    researcher = data["nodes"][2]
    researcher["type"] = "subworkflow"
    researcher["config"] = {
        "workflow_version_id": "cbe96ac1-b959-4277-a198-d29fe1036186",
        "inputs": {"request": {"from_state": "$.request"}},
        "outputs": {"result": {"to_state": "$.delegates.researcher"}},
    }

    assert "workflow.supervisor.invalid_target_type" in {issue.code for issue in issues(data)}


def test_supervisor_self_target_is_rejected() -> None:
    data = supervisor_workflow_data()
    data["nodes"][1]["config"]["allowed_targets"]["supervisor"] = {"task_field": "task"}

    assert "workflow.supervisor.self_target" in {issue.code for issue in issues(data)}


@pytest.mark.parametrize(
    "change",
    [
        "scalar_target_input",
        "task_field_collision",
        "ordinary_target_edge",
        "finish_path_mismatch",
    ],
)
def test_invalid_supervisor_mappings_are_rejected(change: str) -> None:
    data = supervisor_workflow_data()
    researcher = data["nodes"][2]
    if change == "scalar_target_input":
        researcher["config"].pop("inputs")
        researcher["config"]["input_path"] = "$.request"
    elif change == "task_field_collision":
        researcher["config"]["inputs"]["task"] = {"from_state": "$.request"}
    elif change == "ordinary_target_edge":
        data["edges"].append({"source": "input", "target": "researcher"})
    else:
        data["nodes"][1]["config"]["finish_output"] = {"to_state": "$.other"}

    assert "workflow.supervisor.invalid_mapping" in {issue.code for issue in issues(data)}


def test_unknown_supervisor_decision_schema_is_rejected() -> None:
    data = supervisor_workflow_data()
    data["nodes"][1]["config"]["decision_schema"] = "custom_decision"

    assert "workflow.supervisor.invalid_decision_contract" in {issue.code for issue in issues(data)}


def test_handoff_and_finish_decisions_are_strict_and_typed() -> None:
    handoff = SupervisorDecision.model_validate(
        {"action": "handoff", "target": "researcher", "task": "Find sources."}
    )
    finish = SupervisorDecision.model_validate(
        {"action": "finish", "result": {"summary": "Complete"}}
    )

    assert isinstance(handoff.root, HandoffDecision)
    assert handoff.root.target == "researcher"
    assert isinstance(finish.root, FinishDecision)
    assert finish.root.result == {"summary": "Complete"}

    invalid = [
        {"action": "handoff", "target": "researcher"},
        {"action": "handoff", "target": ["researcher", "reviewer"], "task": "Work"},
        {"action": "finish", "target": "reviewer", "result": "done"},
    ]
    for decision in invalid:
        with pytest.raises(ValidationError):
            SupervisorDecision.model_validate(decision)


def test_empty_allowed_targets_is_rejected_during_config_parsing() -> None:
    data = deepcopy(supervisor_workflow_data())
    data["nodes"][1]["config"]["allowed_targets"] = {}

    assert "workflow.node.invalid_config" in {issue.code for issue in issues(data)}
