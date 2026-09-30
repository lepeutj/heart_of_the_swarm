from copy import deepcopy
from uuid import UUID

import pytest

from heart_of_the_swarm.workflows import (
    WorkflowSpec,
    WorkflowValidationError,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConditionNodeConfig,
    InputNodeConfig,
    LLMNodeConfig,
    OutputNodeConfig,
    ToolNodeConfig,
    TransformNodeConfig,
)


def workflow_data() -> dict:
    return {
        "schema_version": "1",
        "id": "4fc4fba0-f38c-4b03-95a4-d478f4636273",
        "name": "Research workflow",
        "description": "Collect, process, and route information.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
        },
        "output_schema": {"type": "object"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "prepare",
                "type": "transform",
                "name": "Prepare",
                "config": {
                    "assign": {
                        "$.target_url": "https://example.com",
                        "$.request_copy": {"from_state": "$.request"},
                    }
                },
            },
            {
                "id": "read",
                "type": "tool",
                "name": "Read",
                "config": {
                    "tool": "document_reader",
                    "arguments": {"url": {"from_state": "$.target_url"}},
                    "output_path": "$.document",
                },
            },
            {
                "id": "summarize",
                "type": "llm",
                "name": "Summarize",
                "config": {
                    "prompt": "Summarize the document.",
                    "model": {"provider": "test", "model_id": "test-model"},
                    "input_path": "$.document",
                    "output_path": "$.summary",
                },
            },
            {
                "id": "research",
                "type": "agent",
                "name": "Research",
                "config": {
                    "agent": {
                        "type": "inline",
                        "spec": {
                            "name": "ResearchAgent",
                            "goal": "Verify the summary",
                            "instructions": "Use the available sources.",
                            "model": {"provider": "test", "model_id": "test-model"},
                            "tools": ["web_search"],
                        },
                    },
                    "input_path": "$.summary",
                    "output_path": "$.verification",
                },
            },
            {
                "id": "verified",
                "type": "condition",
                "name": "Verified?",
                "config": {},
            },
            {
                "id": "success",
                "type": "output",
                "name": "Success",
                "config": {"output_path": "$.verification"},
            },
            {
                "id": "fallback",
                "type": "output",
                "name": "Fallback",
                "config": {"output_path": "$.summary"},
            },
        ],
        "edges": [
            {"source": "input", "target": "prepare"},
            {"source": "prepare", "target": "read"},
            {"source": "read", "target": "summarize"},
            {"source": "summarize", "target": "research"},
            {"source": "research", "target": "verified"},
            {
                "source": "verified",
                "target": "success",
                "condition": {
                    "path": "$.verification.valid",
                    "operator": "equals",
                    "value": True,
                },
            },
            {"source": "verified", "target": "fallback"},
        ],
    }


def validator() -> WorkflowValidator:
    return WorkflowValidator(
        tool_names=["web_search", "document_reader", "calculator"],
        provider_names=["test"],
    )


def issue_codes(data: dict) -> set[str]:
    with pytest.raises(WorkflowValidationError) as caught:
        validator().validate(WorkflowSpec.model_validate(data))
    return {issue.code for issue in caught.value.issues}


def test_valid_workflow_is_converted_to_typed_node_configs() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(workflow_data()))
    assert workflow.id == UUID("4fc4fba0-f38c-4b03-95a4-d478f4636273")
    expected = [
        InputNodeConfig,
        TransformNodeConfig,
        ToolNodeConfig,
        LLMNodeConfig,
        AgentNodeConfig,
        ConditionNodeConfig,
        OutputNodeConfig,
        OutputNodeConfig,
    ]
    assert [type(node.config) for node in workflow.nodes] == expected


def test_duplicate_node_ids_are_rejected() -> None:
    data = workflow_data()
    data["nodes"].append(deepcopy(data["nodes"][0]))
    assert "workflow.node.duplicate_id" in issue_codes(data)


def test_unknown_edge_endpoints_are_rejected() -> None:
    data = workflow_data()
    data["edges"].append({"source": "missing", "target": "also_missing"})
    codes = issue_codes(data)
    assert "workflow.edge.unknown_source" in codes
    assert "workflow.edge.unknown_target" in codes


def test_condition_requires_a_conditional_edge_and_one_fallback() -> None:
    data = workflow_data()
    data["edges"] = [edge for edge in data["edges"] if edge["source"] != "verified"]
    codes = issue_codes(data)
    assert "workflow.condition.missing_branch" in codes
    assert "workflow.condition.missing_fallback" in codes


def test_condition_rejects_multiple_fallback_edges() -> None:
    data = workflow_data()
    data["edges"].append({"source": "verified", "target": "success"})
    assert "workflow.condition.multiple_fallbacks" in issue_codes(data)


def test_conditional_edge_is_rejected_on_regular_node() -> None:
    data = workflow_data()
    data["edges"][0]["condition"] = {
        "path": "$.enabled",
        "operator": "equals",
        "value": True,
    }
    assert "workflow.edge.condition_not_allowed" in issue_codes(data)


def test_cycle_is_rejected_with_machine_readable_error() -> None:
    data = workflow_data()
    data["edges"].append({"source": "success", "target": "prepare"})
    codes = issue_codes(data)
    assert "workflow.cycle_detected" in codes
    assert "workflow.output.has_outgoing_edge" in codes


def test_unreachable_node_and_path_without_output_are_rejected() -> None:
    data = workflow_data()
    data["nodes"].append(
        {
            "id": "orphan",
            "type": "transform",
            "name": "Orphan",
            "config": {"assign": {"$.unused": True}},
        }
    )
    assert "workflow.node.unreachable" in issue_codes(data)

    data = workflow_data()
    data["nodes"].append(
        {
            "id": "dead_end",
            "type": "transform",
            "name": "Dead end",
            "config": {"assign": {"$.unused": True}},
        }
    )
    data["edges"].insert(
        -1,
        {
            "source": "verified",
            "target": "dead_end",
            "condition": {"path": "$.failed", "operator": "equals", "value": True},
        },
    )
    assert "workflow.node.no_output_path" in issue_codes(data)


def test_entrypoint_must_be_an_input_node() -> None:
    data = workflow_data()
    data["entrypoint"] = "prepare"
    assert "workflow.entrypoint.not_input" in issue_codes(data)


def test_unknown_tool_and_provider_are_rejected() -> None:
    data = workflow_data()
    data["nodes"][2]["config"]["tool"] = "shell"
    data["nodes"][3]["config"]["model"]["provider"] = "unknown"
    codes = issue_codes(data)
    assert "workflow.tool.unknown" in codes
    assert "workflow.provider.unknown" in codes


def test_inline_agent_capabilities_are_validated() -> None:
    data = workflow_data()
    agent = data["nodes"][4]["config"]["agent"]["spec"]
    agent["model"]["provider"] = "unknown"
    agent["tools"] = ["shell"]

    codes = issue_codes(data)

    assert "workflow.provider.unknown" in codes
    assert "workflow.tool.unknown" in codes


def test_saved_agent_does_not_duplicate_agent_configuration() -> None:
    data = workflow_data()
    data["nodes"][4]["config"]["agent"] = {
        "type": "saved",
        "agent_version_id": "f5427628-42a7-4698-9e8c-7489da8a7a41",
    }

    workflow = validator().validate(WorkflowSpec.model_validate(data))
    config = workflow.nodes[4].config

    assert isinstance(config, AgentNodeConfig)
    assert config.agent.type == "saved"


def test_invalid_json_schema_is_rejected() -> None:
    data = workflow_data()
    data["input_schema"] = {"type": "not-a-json-schema-type"}
    assert "workflow.schema.invalid" in issue_codes(data)


def test_invalid_nested_state_reference_is_rejected() -> None:
    data = workflow_data()
    data["nodes"][2]["config"]["arguments"] = {"url": {"request": {"from_state": "request.url"}}}
    assert "workflow.state.invalid_reference" in issue_codes(data)


def test_validation_issues_identify_the_node_and_field() -> None:
    data = workflow_data()
    data["edges"] = [edge for edge in data["edges"] if edge["source"] != "verified"]
    with pytest.raises(WorkflowValidationError) as caught:
        validator().validate(WorkflowSpec.model_validate(data))
    issue = next(
        issue
        for issue in caught.value.issues
        if issue.code == "workflow.condition.missing_fallback"
    )
    assert issue.node_id == "verified"
    assert issue.field == "edges"
