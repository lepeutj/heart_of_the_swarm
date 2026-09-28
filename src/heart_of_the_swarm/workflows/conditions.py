from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from heart_of_the_swarm.workflows.enums import ConditionOperator
from heart_of_the_swarm.workflows.state import StatePath, StatePathError, get_path, path_exists


class ConditionEvaluationError(ValueError):
    pass


class ConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: StatePath
    operator: ConditionOperator
    value: Any = None

    @model_validator(mode="after")
    def value_matches_operator(self) -> "ConditionSpec":
        requires_value = self.operator not in {
            ConditionOperator.EXISTS,
            ConditionOperator.NOT_EXISTS,
        }
        if requires_value and "value" not in self.model_fields_set:
            raise ValueError(f"operator '{self.operator}' requires a value")
        return self


def evaluate_condition(condition: ConditionSpec, state: dict[str, Any]) -> bool:
    if condition.operator == ConditionOperator.EXISTS:
        return path_exists(state, condition.path)
    if condition.operator == ConditionOperator.NOT_EXISTS:
        return not path_exists(state, condition.path)

    try:
        actual = get_path(state, condition.path)
        if condition.operator == ConditionOperator.EQUALS:
            return actual == condition.value
        if condition.operator == ConditionOperator.NOT_EQUALS:
            return actual != condition.value
        if condition.operator == ConditionOperator.CONTAINS:
            return condition.value in actual
        if condition.operator == ConditionOperator.GREATER_THAN:
            return actual > condition.value
        if condition.operator == ConditionOperator.LESS_THAN:
            return actual < condition.value
    except (StatePathError, TypeError) as exc:
        raise ConditionEvaluationError(
            f"could not evaluate '{condition.operator}' at '{condition.path}'"
        ) from exc
    raise ConditionEvaluationError(f"unsupported condition operator: {condition.operator}")
