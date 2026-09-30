from uuid import UUID

import pytest

from heart_of_the_swarm.workflows import (
    CompiledConditionNode,
    CompiledInputNode,
    CompiledOutputNode,
    CompiledToolNode,
    CompiledTransformNode,
    ExecutionPlan,
    WorkflowCompilationError,
    WorkflowCompiler,
    WorkflowSpec,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.configs import OutputNodeConfig


def validator() -> WorkflowValidator:
    return WorkflowValidator(tool_names=["calculator"], provider_names=["test"])


def sequential_data() -> dict:
    return {
        "schema_version": "1",
        "id": "4fc4fba0-f38c-4b03-95a4-d478f4636273",
        "name": "Prepare result",
        "description": "Copy the request into the result.",
        "input_schema": {"type": "object", "required": ["request"]},
        "output_schema": {"type": "string"},
        "entrypoint": "input",
        "nodes": [
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.result"},
            },
            {
                "id": "prepare",
                "type": "transform",
                "name": "Prepare",
                "config": {"assign": {"$.result": {"from_state": "$.request"}}},
            },
            {"id": "input", "type": "input", "name": "Input", "config": {}},
        ],
        "edges": [
            {"source": "input", "target": "prepare"},
            {"source": "prepare", "target": "output"},
        ],
    }


def conditional_data() -> dict:
    return {
        "schema_version": "1",
        "id": "44d9df8d-08b1-4e41-81c0-c104b84e0679",
        "name": "Route result",
        "description": "Route valid and invalid results.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {"id": "route", "type": "condition", "name": "Valid?", "config": {}},
            {
                "id": "fallback",
                "type": "output",
                "name": "Fallback",
                "config": {"output_path": "$.fallback"},
            },
            {
                "id": "success",
                "type": "output",
                "name": "Success",
                "config": {"output_path": "$.result"},
            },
        ],
        "edges": [
            {"source": "input", "target": "route"},
            {
                "source": "route",
                "target": "success",
                "label": "valid",
                "condition": {"path": "$.valid", "operator": "equals", "value": True},
            },
            {
                "source": "route",
                "target": "fallback",
                "label": "missing",
                "condition": {"path": "$.missing", "operator": "exists"},
            },
            {"source": "route", "target": "fallback", "label": "default"},
        ],
    }


def test_sequential_workflow_compiles_to_serializable_execution_plan() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(sequential_data()))

    plan = WorkflowCompiler().compile(workflow)

    assert plan.workflow_id == UUID("4fc4fba0-f38c-4b03-95a4-d478f4636273")
    assert plan.entrypoint == "input"
    assert plan.execution_order == ("input", "prepare", "output")
    nodes = {node.id: node for node in plan.nodes}
    assert isinstance(nodes["input"], CompiledInputNode)
    assert nodes["input"].next_node == "prepare"
    assert isinstance(nodes["prepare"], CompiledTransformNode)
    assert nodes["prepare"].dependencies == ("input",)
    assert nodes["prepare"].next_node == "output"
    assert isinstance(nodes["output"], CompiledOutputNode)
    assert nodes["output"].dependencies == ("prepare",)
    assert ExecutionPlan.model_validate_json(plan.model_dump_json()) == plan


def test_conditional_workflow_preserves_route_order_and_fallback() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(conditional_data()))

    plan = WorkflowCompiler().compile(workflow)

    route = next(node for node in plan.nodes if node.id == "route")
    assert isinstance(route, CompiledConditionNode)
    assert [(item.target, item.label) for item in route.routes] == [
        ("success", "valid"),
        ("fallback", "missing"),
    ]
    assert route.fallback == "fallback"
    assert plan.execution_order == ("input", "route", "fallback", "success")


def test_compilation_is_deterministic_and_copies_workflow_metadata() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(conditional_data()))
    compiler = WorkflowCompiler()

    first = compiler.compile(workflow)
    second = compiler.compile(workflow)

    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    workflow.input_schema["title"] = "Changed after compilation"
    assert "title" not in first.input_schema


def test_compiler_rejects_raw_workflow_spec() -> None:
    raw = WorkflowSpec.model_validate(sequential_data())

    with pytest.raises(WorkflowCompilationError) as caught:
        WorkflowCompiler().compile(raw)  # type: ignore[arg-type]

    assert [issue.code for issue in caught.value.issues] == ["workflow.compiler.unvalidated_input"]


def test_compiler_rejects_mismatched_validated_node_config() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(sequential_data()))
    workflow.nodes[2] = workflow.nodes[2].model_copy(
        update={"config": OutputNodeConfig(output_path="$.result")}
    )

    with pytest.raises(WorkflowCompilationError) as caught:
        WorkflowCompiler().compile(workflow)

    assert [issue.model_dump() for issue in caught.value.issues] == [
        {
            "code": "workflow.compiler.config_type_mismatch",
            "message": "Node 'input' has configuration incompatible with type 'input'.",
            "node_id": "input",
        }
    ]


def test_compiler_compiles_registered_tool_node() -> None:
    data = sequential_data()
    data["nodes"][1] = {
        "id": "prepare",
        "type": "tool",
        "name": "Calculate",
        "config": {
            "tool": "calculator",
            "arguments": {"expression": "1 + 1"},
            "output_path": "$.result",
        },
    }
    workflow = validator().validate(WorkflowSpec.model_validate(data))

    plan = WorkflowCompiler().compile(workflow)

    tool = next(node for node in plan.nodes if node.id == "prepare")
    assert isinstance(tool, CompiledToolNode)
    assert tool.config.tool == "calculator"
    assert tool.config.arguments == {"expression": "1 + 1"}
    assert tool.config.output_path == "$.result"
    assert tool.next_node == "output"


def test_compiler_rejects_unsupported_runtime_nodes_without_partial_plan() -> None:
    data = sequential_data()
    data["nodes"][1] = {
        "id": "prepare",
        "type": "llm",
        "name": "Summarize",
        "config": {
            "prompt": "Summarize the input.",
            "model": {"provider": "test", "model_id": "test-model"},
            "input_path": "$.request",
            "output_path": "$.result",
        },
    }
    workflow = validator().validate(WorkflowSpec.model_validate(data))

    with pytest.raises(WorkflowCompilationError) as caught:
        WorkflowCompiler().compile(workflow)

    assert [issue.model_dump() for issue in caught.value.issues] == [
        {
            "code": "workflow.compiler.unsupported_node",
            "message": "Node type 'llm' is not supported by this compiler.",
            "node_id": "prepare",
        }
    ]
