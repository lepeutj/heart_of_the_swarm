from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from heart_of_the_swarm.workflows.spec import StateReducer


def reduce_parallel_values(
    reducer: StateReducer,
    contributions: Sequence[Any],
) -> Any:
    """Combine branch values in declared order without mutating their inputs."""
    if not contributions:
        raise ValueError("parallel reduction requires at least one contribution")
    if reducer == StateReducer.REPLACE:
        if len(contributions) != 1:
            raise ValueError("replace cannot combine concurrent contributions")
        return deepcopy(contributions[0])
    if reducer == StateReducer.APPEND:
        return _append(contributions)
    if reducer == StateReducer.MERGE_DICT:
        return _merge_dict(contributions)
    raise ValueError(f"unsupported state reducer: {reducer}")


def _append(contributions: Sequence[Any]) -> list[Any]:
    """Concatenate list contributions while preserving their supplied order."""
    result: list[Any] = []
    for contribution in contributions:
        if not isinstance(contribution, list):
            raise TypeError("append reducer requires list contributions")
        result.extend(deepcopy(contribution))
    return result


def _merge_dict(contributions: Sequence[Any]) -> dict[str, Any]:
    """Merge mappings shallowly and reject keys contributed more than once."""
    result: dict[str, Any] = {}
    for contribution in contributions:
        if not isinstance(contribution, Mapping):
            raise TypeError("merge_dict reducer requires object contributions")
        duplicate_keys = set(result) & set(contribution)
        if duplicate_keys:
            names = ", ".join(sorted(str(key) for key in duplicate_keys))
            raise ValueError(f"merge_dict received duplicate keys: {names}")
        result.update(deepcopy(dict(contribution)))
    return result
