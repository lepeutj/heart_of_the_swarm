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
    OutputNodeConfig,
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
                "id": "summarize",
                "type": "agent",
                "name": "Summarize",
                "config": {
                    "source": {
                        "type": "inline",
                        "agent": {
                            "name": "ResearchAgent",
                            "goal": "Research and summarize",
                            "instructions": "Use the available sources.",
                            "model": {"provider": "test", "model_id": "test-model"},
                            "tools": ["web_search"],
                        },
                    },
                    "input_path": "$.request_copy",
                    "output_path": "$.summary",
                },
            },
            {
                "id": "research",
                "type": "agent",
                "name": "Research",
                "config": {
                    "source": {
                        "type": "version",
                        "agent_version_id": "f5427628-42a7-4698-9e8c-7489da8a7a41",
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
            {"source": "prepare", "target": "summarize"},
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
        AgentNodeConfig,
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


def test_condition_rejects_routes_with_the_same_target() -> None:
    data = workflow_data()
    data["edges"][-1]["target"] = data["edges"][4]["target"]
    assert "workflow.condition.duplicate_target" in issue_codes(data)


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


def test_v2_accepts_one_bounded_conditional_back_edge() -> None:
    data = workflow_data()
    data["schema_version"] = "2"
    data["edges"][-1] = {
        "source": "verified",
        "target": "summarize",
        "loop": {"id": "revision", "max_iterations": 3},
    }
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "fallback"]

    workflow = validator().validate(WorkflowSpec.model_validate(data))

    assert workflow.schema_version == "2"
    assert workflow.edges[-1].loop is not None
    assert workflow.edges[-1].loop.id == "revision"


def test_loop_contract_rejects_v1_forward_and_multiple_loop_edges() -> None:
    data = workflow_data()
    data["edges"][-1]["loop"] = {"id": "revision", "max_iterations": 3}
    assert "workflow.loop.requires_schema_v2" in issue_codes(data)

    data["schema_version"] = "2"
    assert "workflow.loop.not_back_edge" in issue_codes(data)

    data["edges"][-1]["target"] = "summarize"
    data["edges"][4]["loop"] = {"id": "exit", "max_iterations": 1}
    assert "workflow.loop.multiple_not_supported" in issue_codes(data)


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


def test_unknown_inline_agent_tool_and_provider_are_rejected() -> None:
    data = workflow_data()
    agent = data["nodes"][2]["config"]["source"]["agent"]
    agent["model"]["provider"] = "unknown"
    agent["tools"] = ["shell"]
    codes = issue_codes(data)
    assert "workflow.tool.unknown" in codes
    assert "workflow.provider.unknown" in codes


def test_saved_agent_does_not_duplicate_agent_configuration() -> None:
    data = workflow_data()

    workflow = validator().validate(WorkflowSpec.model_validate(data))
    config = workflow.nodes[3].config

    assert isinstance(config, AgentNodeConfig)
    assert config.source.agent_version_id == UUID("f5427628-42a7-4698-9e8c-7489da8a7a41")


def test_legacy_saved_agent_configuration_is_migrated() -> None:
    data = workflow_data()
    config = data["nodes"][3]["config"]
    version_id = config.pop("source")["agent_version_id"]
    config["agent_version_id"] = version_id

    workflow = validator().validate(WorkflowSpec.model_validate(data))
    migrated = workflow.nodes[3].config

    assert isinstance(migrated, AgentNodeConfig)
    assert migrated.source.agent_version_id == UUID(version_id)


def test_connector_output_destinations_must_not_overlap() -> None:
    data = workflow_data()
    data["nodes"].insert(
        1,
        {
            "id": "connector",
            "type": "connector",
            "name": "Connector",
            "config": {
                "capability_id": "calculator",
                "outputs": {
                    "result": {"to_state": "$.data"},
                    "details": {"to_state": "$.data.details"},
                },
            },
        },
    )
    data["edges"][0] = {"source": "input", "target": "connector"}
    data["edges"].insert(1, {"source": "connector", "target": "prepare"})

    assert "workflow.node.invalid_config" in issue_codes(data)


def test_invalid_json_schema_is_rejected() -> None:
    data = workflow_data()
    data["input_schema"] = {"type": "not-a-json-schema-type"}
    assert "workflow.schema.invalid" in issue_codes(data)


def test_invalid_nested_state_reference_is_rejected() -> None:
    data = workflow_data()
    data["nodes"][1]["config"]["assign"] = {"$.copy": {"request": {"from_state": "request.url"}}}
    assert "workflow.state.invalid_reference" in issue_codes(data)


def test_multiple_agent_outputs_require_a_structured_response_schema() -> None:
    data = workflow_data()
    data["nodes"][2]["config"].pop("input_path")
    data["nodes"][2]["config"].pop("output_path")
    data["nodes"][2]["config"]["inputs"] = {"request": {"from_state": "$.request_copy"}}
    data["nodes"][2]["config"]["outputs"] = {
        "answer": {"to_state": "$.research.answer"},
        "sources": {"to_state": "$.research.sources"},
    }

    assert "workflow.node.invalid_config" in issue_codes(data)


def test_agent_outputs_must_be_required_response_schema_properties() -> None:
    data = workflow_data()
    config = data["nodes"][2]["config"]
    config.pop("input_path")
    config.pop("output_path")
    config["inputs"] = {"request": {"from_state": "$.request_copy"}}
    config["outputs"] = {
        "answer": {"to_state": "$.research.answer"},
        "sources": {"to_state": "$.research.sources"},
    }
    config["response_schema"] = {
        "title": "Research",
        "description": "Research result",
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    codes = issue_codes(data)
    assert "workflow.agent.output_not_in_schema" in codes


def test_agent_output_destinations_must_not_overlap() -> None:
    data = workflow_data()
    config = data["nodes"][2]["config"]
    config.pop("input_path")
    config.pop("output_path")
    config["inputs"] = {"request": {"from_state": "$.request_copy"}}
    config["outputs"] = {
        "answer": {"to_state": "$.research"},
        "sources": {"to_state": "$.research.sources"},
    }
    config["response_schema"] = {
        "title": "Research",
        "description": "Research result",
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "sources": {"type": "array"},
        },
        "required": ["answer", "sources"],
    }

    assert "workflow.node.invalid_config" in issue_codes(data)


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
