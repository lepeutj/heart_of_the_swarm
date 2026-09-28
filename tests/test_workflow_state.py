import pytest
from pydantic import TypeAdapter, ValidationError

from heart_of_the_swarm.workflows import ConditionSpec, evaluate_condition
from heart_of_the_swarm.workflows.conditions import ConditionEvaluationError
from heart_of_the_swarm.workflows.state import (
    StatePath,
    StatePathError,
    get_path,
    path_exists,
    resolve_arguments,
    set_path,
)


def test_state_path_accepts_only_dot_separated_identifiers() -> None:
    adapter = TypeAdapter(StatePath)
    assert adapter.validate_python("$.research.sources") == "$.research.sources"
    for invalid in ("research.sources", "$", "$.items[0]", "$.items.*", "$.a..b"):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_state_access_creates_and_reads_nested_values() -> None:
    state: dict[str, object] = {"request": "topic"}
    set_path(state, "$.research.summary", "result")
    assert get_path(state, "$.request") == "topic"
    assert get_path(state, "$.research.summary") == "result"
    assert path_exists(state, "$.research.summary")
    assert not path_exists(state, "$.research.missing")


def test_state_access_rejects_traversing_a_scalar() -> None:
    with pytest.raises(StatePathError, match="non-object"):
        set_path({"research": "text"}, "$.research.summary", "result")

    with pytest.raises(StatePathError, match="invalid state path"):
        path_exists({}, "research.summary")


def test_argument_resolution_supports_literals_and_state_references() -> None:
    state = {"request": {"url": "https://example.com"}, "limit": 3}
    arguments = resolve_arguments(
        {
            "url": {"from_state": "$.request.url"},
            "options": {"limit": {"from_state": "$.limit"}, "language": "en"},
        },
        state,
    )
    assert arguments == {
        "url": "https://example.com",
        "options": {"limit": 3, "language": "en"},
    }


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"path": "$.score", "operator": "equals", "value": 10}, True),
        ({"path": "$.score", "operator": "not_equals", "value": 4}, True),
        ({"path": "$.score", "operator": "greater_than", "value": 4}, True),
        ({"path": "$.score", "operator": "less_than", "value": 20}, True),
        ({"path": "$.tags", "operator": "contains", "value": "verified"}, True),
        ({"path": "$.score", "operator": "exists"}, True),
        ({"path": "$.missing", "operator": "not_exists"}, True),
    ],
)
def test_condition_language(condition: dict[str, object], expected: bool) -> None:
    state = {"score": 10, "tags": ["verified", "primary"]}
    assert evaluate_condition(ConditionSpec.model_validate(condition), state) is expected


def test_condition_requires_a_value_when_applicable() -> None:
    with pytest.raises(ValidationError, match="requires a value"):
        ConditionSpec.model_validate({"path": "$.score", "operator": "equals"})


def test_condition_reports_incompatible_values() -> None:
    condition = ConditionSpec(path="$.score", operator="contains", value="x")
    with pytest.raises(ConditionEvaluationError):
        evaluate_condition(condition, {"score": 10})
