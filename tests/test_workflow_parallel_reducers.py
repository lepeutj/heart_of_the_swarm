from copy import deepcopy

import pytest
from test_workflow_parallel_validation import parallel_workflow_data

from heart_of_the_swarm.workflows.execution.parallel import (
    build_parallel_runtime_region,
    merge_parallel_states,
)
from heart_of_the_swarm.workflows.execution.reducers import reduce_parallel_values
from heart_of_the_swarm.workflows.spec import StateReducer, WorkflowSpec
from heart_of_the_swarm.workflows.validation import WorkflowValidator


def test_replace_accepts_one_value_without_sharing_mutable_state() -> None:
    contribution = {"items": [1]}

    result = reduce_parallel_values(StateReducer.REPLACE, [contribution])
    result["items"].append(2)

    assert contribution == {"items": [1]}


def test_replace_rejects_concurrent_contributions() -> None:
    with pytest.raises(ValueError, match="replace cannot combine concurrent"):
        reduce_parallel_values(StateReducer.REPLACE, ["left", "right"])


def test_append_preserves_declared_contribution_order() -> None:
    contributions = [[{"branch": "left"}], [{"branch": "right"}]]
    original = deepcopy(contributions)

    result = reduce_parallel_values(StateReducer.APPEND, contributions)

    assert result == [{"branch": "left"}, {"branch": "right"}]
    assert contributions == original


def test_append_rejects_non_list_contribution() -> None:
    with pytest.raises(TypeError, match="requires list contributions"):
        reduce_parallel_values(StateReducer.APPEND, [[1], {"value": 2}])


def test_merge_dict_preserves_declared_contribution_order() -> None:
    contributions = [{"left": {"value": 1}}, {"right": {"value": 2}}]
    original = deepcopy(contributions)

    result = reduce_parallel_values(StateReducer.MERGE_DICT, contributions)

    assert list(result) == ["left", "right"]
    assert result == {"left": {"value": 1}, "right": {"value": 2}}
    assert contributions == original


def test_merge_dict_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate keys: shared"):
        reduce_parallel_values(
            StateReducer.MERGE_DICT,
            [{"shared": "left"}, {"shared": "right"}],
        )


def test_merge_dict_rejects_non_object_contribution() -> None:
    with pytest.raises(TypeError, match="requires object contributions"):
        reduce_parallel_values(StateReducer.MERGE_DICT, [{"left": 1}, ["right"]])


def test_reducer_rejects_empty_contributions() -> None:
    with pytest.raises(ValueError, match="requires at least one contribution"):
        reduce_parallel_values(StateReducer.APPEND, [])


def test_runtime_region_preserves_branch_and_reducer_declaration_order() -> None:
    data = parallel_workflow_data(
        left_path="$.results",
        right_path="$.results",
        state_schema={
            "$.results": {"schema": {"type": "array"}, "reducer": "append"},
        },
    )
    workflow = WorkflowValidator([], []).validate(WorkflowSpec.model_validate(data))

    region = build_parallel_runtime_region(workflow)

    assert region is not None
    assert region.split_node_id == "input"
    assert region.join_node_id == "synthesize"
    assert [branch.entry_node_id for branch in region.branches] == ["research", "analysis"]
    assert [branch.write_paths for branch in region.branches] == [
        ("$.results",),
        ("$.results",),
    ]
    assert region.reductions[0].path == "$.results"
    assert region.reductions[0].reducer == StateReducer.APPEND
    assert region.reductions[0].branch_ids == ("input:0", "input:1")
    assert region.branch_for_node("analysis") == region.branches[1]
    assert region.branch_for_node("synthesize") is None


def test_parallel_state_merge_uses_declared_branch_order() -> None:
    data = parallel_workflow_data(
        left_path="$.results",
        right_path="$.results",
        state_schema={
            "$.results": {"schema": {"type": "array"}, "reducer": "append"},
        },
    )
    workflow = WorkflowValidator([], []).validate(WorkflowSpec.model_validate(data))
    region = build_parallel_runtime_region(workflow)
    assert region is not None

    result = merge_parallel_states(
        {"request": "topic"},
        region,
        {
            "input:0": {"request": "topic", "results": ["left"]},
            "input:1": {"request": "topic", "results": ["right"]},
        },
        {"input:0": ("research",), "input:1": ("analysis",)},
    )

    assert result == {"request": "topic", "results": ["left", "right"]}


def test_parallel_state_merge_ignores_writes_from_unexecuted_route_nodes() -> None:
    data = parallel_workflow_data(
        left_path="$.left",
        right_path="$.right",
        state_schema={
            "$.left": {"schema": {"type": "string"}},
            "$.right": {"schema": {"type": "string"}},
        },
    )
    workflow = WorkflowValidator([], []).validate(WorkflowSpec.model_validate(data))
    region = build_parallel_runtime_region(workflow)
    assert region is not None

    result = merge_parallel_states(
        {"left": "original", "right": "original"},
        region,
        {
            "input:0": {"left": "updated", "right": "original"},
            "input:1": {"left": "original", "right": "original"},
        },
        {"input:0": ("research",), "input:1": ()},
    )

    assert result == {"left": "updated", "right": "original"}
