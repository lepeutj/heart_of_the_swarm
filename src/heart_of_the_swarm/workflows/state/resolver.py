from typing import Any

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.workflows.state.paths import StatePath, get_path


class StateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_state: StatePath


def resolve_value(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"from_state"}:
            reference = StateReference.model_validate(value)
            return get_path(state, reference.from_state)
        return {key: resolve_value(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, state) for item in value]
    return value
