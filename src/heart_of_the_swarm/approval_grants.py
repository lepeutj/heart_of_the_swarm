import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationRequest,
    Principal,
    PrincipalKind,
)


class ApprovalGrantStatus(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"


class CapabilityApprovalRequest(BaseModel):
    """Safe persisted identity of one capability invocation awaiting approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization: AuthorizationRequest
    arguments_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_context(self) -> "CapabilityApprovalRequest":
        if self.authorization.action != AuthorizationAction.CAPABILITY_INVOKE:
            raise ValueError("capability approval requires capability.invoke")
        context = self.authorization.context
        if context.run_id is None or context.node_id is None:
            raise ValueError("capability approval requires run and node context")
        return self


class CapabilityApprovalResume(BaseModel):
    """Trusted resume value created after resolving one capability interruption."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    grant_id: UUID | None = None

    @model_validator(mode="after")
    def validate_grant(self) -> "CapabilityApprovalResume":
        if self.approved != (self.grant_id is not None):
            raise ValueError("approved capability resumes require exactly one grant")
        return self


class CapabilityApprovalInterruptResponse(BaseModel):
    """LangGraph resume envelope before the runtime verifies grant presence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    grant_id: UUID | None = None


class ApprovalGrant(BaseModel):
    """One expiring, single-use permission for an exact capability invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    interruption_id: UUID
    source_run_id: UUID
    continuation_run_id: UUID
    node_id: str = Field(min_length=1, max_length=300)
    principal: Principal
    action: AuthorizationAction
    resource: str
    arguments_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: Principal
    status: ApprovalGrantStatus = Field(default=ApprovalGrantStatus.ACTIVE)
    created_at: datetime
    expires_at: datetime
    consumed_by_run_id: UUID | None = None
    consumed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_capability_grant(self) -> "ApprovalGrant":
        """Reject grants that could authorize a broader or ambiguous operation."""
        AuthorizationRequest(
            principal=self.principal,
            action=self.action,
            resource=self.resource,
        )
        if self.action != AuthorizationAction.CAPABILITY_INVOKE:
            raise ValueError("approval grants support only capability invocation")
        if self.approved_by.kind != PrincipalKind.USER:
            raise ValueError("approval grants require a user approver")
        if self.created_at.tzinfo is None:
            raise ValueError("approval grant creation time must be timezone-aware")
        if self.expires_at.tzinfo is None:
            raise ValueError("approval grant expiry must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("approval grant expiry must be after creation")
        if self.consumed_at is not None and self.consumed_at.tzinfo is None:
            raise ValueError("approval grant consumption time must be timezone-aware")
        consumed = self.status == ApprovalGrantStatus.CONSUMED
        has_consumption = self.consumed_at is not None or self.consumed_by_run_id is not None
        complete_consumption = self.consumed_at is not None and self.consumed_by_run_id is not None
        if (consumed and not complete_consumption) or (not consumed and has_consumption):
            raise ValueError("consumed grants require matching run and timestamp")
        return self

    def permits(
        self,
        request: AuthorizationRequest,
        arguments_fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Match every request dimension without mutating or consuming the grant."""
        checked_at = now or datetime.now(UTC)
        if checked_at.tzinfo is None:
            raise ValueError("approval grant check time must be timezone-aware")
        context = request.context
        return (
            self.status == ApprovalGrantStatus.ACTIVE
            and self.consumed_at is None
            and checked_at < self.expires_at
            and context.run_id == self.continuation_run_id
            and context.node_id == self.node_id
            and request.principal == self.principal
            and request.action == self.action
            and request.resource == self.resource
            and arguments_fingerprint == self.arguments_fingerprint
        )


class ApprovalGrantConsumer(Protocol):
    """Consume a persisted grant without exposing storage to the workflow runtime."""

    async def consume(
        self,
        grant_id: UUID,
        request: AuthorizationRequest,
        arguments_fingerprint: str,
    ) -> ApprovalGrant:
        """Atomically consume an exact active grant before a protected side effect."""


def fingerprint_capability_arguments(arguments: Mapping[str, Any]) -> str:
    """Hash canonical validated arguments without persisting their raw representation."""
    canonical = _canonical_value(arguments)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_value(value: Any) -> Any:
    """Project the supported JSON value set into a stable, secret-free representation."""
    if isinstance(value, SecretStr):
        raise ValueError("secret values cannot be capability fingerprint inputs")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers cannot be capability fingerprint inputs")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetimes cannot be capability fingerprint inputs")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("capability argument object keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError(f"unsupported capability argument type: {type(value).__name__}")
