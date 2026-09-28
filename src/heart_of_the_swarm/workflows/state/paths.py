import re
from typing import Annotated, Any

from pydantic import StringConstraints

_PATH_PATTERN = r"^\$\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
_PATH_RE = re.compile(_PATH_PATTERN)

StatePath = Annotated[str, StringConstraints(pattern=_PATH_PATTERN, max_length=500)]


class StatePathError(ValueError):
    pass


def path_parts(path: str) -> tuple[str, ...]:
    if not _PATH_RE.fullmatch(path):
        raise StatePathError(f"invalid state path: {path}")
    return tuple(path[2:].split("."))


def get_path(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path_parts(path):
        if not isinstance(current, dict) or part not in current:
            raise StatePathError(f"state path does not exist: {path}")
        current = current[part]
    return current


def set_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = path_parts(path)
    current = state
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            existing = {}
            current[part] = existing
        if not isinstance(existing, dict):
            raise StatePathError(f"cannot traverse non-object state path: {path}")
        current = existing
    current[parts[-1]] = value


def path_exists(state: dict[str, Any], path: str) -> bool:
    current: Any = state
    for part in path_parts(path):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True
