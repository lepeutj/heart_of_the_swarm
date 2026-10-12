from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, StringConstraints

SUPERVISOR_DECISION_SCHEMA = "supervisor_decision_v1"
SupervisorTargetId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=64),
]
SupervisorTask = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000),
]


class HandoffDecision(BaseModel):
    """Transfer one explicit task to exactly one allowed agent node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["handoff"]
    target: SupervisorTargetId
    task: SupervisorTask


class FinishDecision(BaseModel):
    """Finish supervisor routing with one JSON-serializable business result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["finish"]
    result: JsonValue


class SupervisorDecision(RootModel):
    """Strict discriminated decision emitted by a supervisor agent."""

    root: Annotated[HandoffDecision | FinishDecision, Field(discriminator="action")]
