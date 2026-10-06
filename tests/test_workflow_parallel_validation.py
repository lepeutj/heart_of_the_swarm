from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from heart_of_the_swarm.workflows import (
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidationError,
    WorkflowValidator,
)
from heart_of_the_swarm.workflows.spec import StateReducer


def parallel_workflow_data(
    *,
    left_path: str = "$.research.result",
    right_path: str = "$.analysis.result",
    state_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "2",
        "id": "79cb2f4a-31ab-42d6-bcb9-ce517bb88d12",
        "name": "Parallel analysis",
        "description": "Run two deterministic branches before synthesis.",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "state_schema": state_schema or {},
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Input", "config": {}},
            {
                "id": "research",
                "type": "transform",
                "name": "Research",
                "config": {"assign": {left_path: ["research"]}},
            },
            {
                "id": "analysis",
                "type": "transform",
                "name": "Analysis",
                "config": {"assign": {right_path: ["analysis"]}},
            },
            {
                "id": "synthesize",
                "type": "transform",
                "name": "Synthesize",
                "config": {"assign": {"$.final": "done"}},
            },
            {
                "id": "output",
                "type": "output",
                "name": "Output",
                "config": {"output_path": "$.final"},
            },
        ],
        "edges": [
            {"source": "input", "target": "research"},
            {"source": "input", "target": "analysis"},
            {"source": "research", "target": "synthesize"},
            {"source": "analysis", "target": "synthesize"},
            {"source": "synthesize", "target": "output"},
        ],
    }


def validator() -> WorkflowValidator:
    return WorkflowValidator([], [])


def validation_issues(data: dict[str, Any]):
    with pytest.raises(WorkflowValidationError) as caught:
        validator().validate(WorkflowSpec.model_validate(data))
    return caught.value.issues


@pytest.mark.parametrize(
    "state_schema",
    [
        {"$.results": {"schema": {"type": "array"}, "reducer": "unknown"}},
        {"results": {"schema": {"type": "array"}, "reducer": "append"}},
    ],
)
def test_state_schema_rejects_unknown_reducers_and_invalid_paths(
    state_schema: dict[str, Any],
) -> None:
    data = parallel_workflow_data(state_schema=state_schema)

    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate(data)


def test_parallel_region_and_typed_state_schema_are_identified() -> None:
    data = parallel_workflow_data(
        state_schema={
            "$.research.result": {"schema": {"type": "array"}, "reducer": "replace"},
            "$.analysis.result": {"schema": {"type": "array"}},
        }
    )

    workflow = validator().validate(WorkflowSpec.model_validate(data))

    assert workflow.state_schema["$.research.result"].reducer == StateReducer.REPLACE
    assert workflow.model_dump(mode="json")["state_schema"]["$.research.result"]["schema"] == {
        "type": "array"
    }
    assert workflow.parallel_region is not None
    assert workflow.parallel_region.split_node_id == "input"
    assert workflow.parallel_region.join_node_id == "synthesize"
    assert [branch.entry_node_id for branch in workflow.parallel_region.branches] == [
        "research",
        "analysis",
    ]


def test_reducers_require_compatible_state_schemas() -> None:
    data = parallel_workflow_data(
        state_schema={
            "$.research.result": {"schema": {"type": "string"}, "reducer": "append"},
            "$.analysis.result": {"schema": {"type": "array"}, "reducer": "merge_dict"},
        }
    )

    issues = validation_issues(data)

    mismatches = [
        issue for issue in issues if issue.code == "workflow.state_schema.reducer_type_mismatch"
    ]
    assert len(mismatches) == 2
    assert {issue.field for issue in mismatches} == {
        "state_schema.$.research.result.reducer",
        "state_schema.$.analysis.result.reducer",
    }


def test_state_schema_requires_v2() -> None:
    data = parallel_workflow_data(
        state_schema={
            "$.result": {"schema": {"type": "object"}},
            "$.result.title": {"schema": {"type": "string"}},
        }
    )
    data["nodes"] = [data["nodes"][0], data["nodes"][3], data["nodes"][4]]
    data["edges"] = [
        {"source": "input", "target": "synthesize"},
        {"source": "synthesize", "target": "output"},
    ]
    data["schema_version"] = "1"

    codes = {issue.code for issue in validation_issues(data)}

    assert "workflow.state_schema.requires_schema_v2" in codes


def test_nested_state_declarations_are_valid_without_concurrent_writes() -> None:
    data = parallel_workflow_data(
        state_schema={
            "$.result": {"schema": {"type": "object"}},
            "$.result.items": {"schema": {"type": "array"}, "reducer": "append"},
        }
    )
    data["nodes"] = [data["nodes"][0], data["nodes"][3], data["nodes"][4]]
    data["edges"] = [
        {"source": "input", "target": "synthesize"},
        {"source": "synthesize", "target": "output"},
    ]

    workflow = validator().validate(WorkflowSpec.model_validate(data))

    assert set(workflow.state_schema) == {"$.result", "$.result.items"}


def test_concurrent_writes_to_distinct_paths_are_valid() -> None:
    workflow = validator().validate(WorkflowSpec.model_validate(parallel_workflow_data()))

    assert workflow.parallel_region is not None
    with pytest.raises(RuntimeError, match="Parallel workflow execution is not implemented"):
        WorkflowGraphFactory().create(workflow)


@pytest.mark.parametrize(
    ("reducer", "schema"),
    [
        ("append", {"type": "array"}),
        ("merge_dict", {"type": "object"}),
    ],
)
def test_combinatory_reducer_allows_same_path_writes(
    reducer: str,
    schema: dict[str, Any],
) -> None:
    data = parallel_workflow_data(
        left_path="$.results",
        right_path="$.results",
        state_schema={"$.results": {"schema": schema, "reducer": reducer}},
    )

    workflow = validator().validate(WorkflowSpec.model_validate(data))

    assert workflow.parallel_region is not None


def test_same_path_without_combinatory_reducer_names_path_and_writers() -> None:
    data = parallel_workflow_data(
        left_path="$.results",
        right_path="$.results",
        state_schema={"$.results": {"schema": {"type": "array"}, "reducer": "replace"}},
    )

    issue = next(
        issue
        for issue in validation_issues(data)
        if issue.code == "workflow.parallel.write_conflict"
    )

    assert "$.results" in issue.message
    assert "analysis" in issue.message
    assert "research" in issue.message
    assert issue.field == "state_schema.$.results"


def test_parent_child_path_conflict_is_always_rejected() -> None:
    data = parallel_workflow_data(
        left_path="$.results",
        right_path="$.results.summary",
        state_schema={"$.results": {"schema": {"type": "object"}, "reducer": "merge_dict"}},
    )

    issue = next(
        issue
        for issue in validation_issues(data)
        if issue.code == "workflow.parallel.write_conflict"
    )

    assert "$.results" in issue.message
    assert "$.results.summary" in issue.message


def test_parallel_split_requires_one_identifiable_join() -> None:
    data = parallel_workflow_data()
    data["nodes"][-1] = {
        "id": "second_output",
        "type": "output",
        "name": "Second output",
        "config": {"output_path": "$.analysis.result"},
    }
    data["edges"] = [
        {"source": "input", "target": "research"},
        {"source": "input", "target": "analysis"},
        {"source": "research", "target": "synthesize"},
        {"source": "analysis", "target": "second_output"},
    ]
    data["nodes"][3] = {
        "id": "synthesize",
        "type": "output",
        "name": "First output",
        "config": {"output_path": "$.research.result"},
    }

    codes = {issue.code for issue in validation_issues(data)}

    assert "workflow.parallel.missing_join" in codes


def test_exclusive_condition_convergence_is_not_parallel() -> None:
    data = parallel_workflow_data()
    data["nodes"][0] = {"id": "input", "type": "input", "name": "Input", "config": {}}
    data["nodes"].insert(1, {"id": "route", "type": "condition", "name": "Route", "config": {}})
    data["edges"] = [
        {"source": "input", "target": "route"},
        {
            "source": "route",
            "target": "research",
            "condition": {"path": "$.use_research", "operator": "equals", "value": True},
        },
        {"source": "route", "target": "analysis"},
        {"source": "research", "target": "synthesize"},
        {"source": "analysis", "target": "synthesize"},
        {"source": "synthesize", "target": "output"},
    ]

    workflow = validator().validate(WorkflowSpec.model_validate(data))

    assert workflow.parallel_region is None


def test_nested_or_multiple_parallel_regions_are_rejected() -> None:
    data = deepcopy(parallel_workflow_data())
    data["nodes"].insert(
        2,
        {"id": "extra", "type": "transform", "name": "Extra", "config": {"assign": {"$.x": 1}}},
    )
    data["edges"].insert(2, {"source": "research", "target": "extra"})

    codes = {issue.code for issue in validation_issues(data)}

    assert "workflow.parallel.multiple_not_supported" in codes
