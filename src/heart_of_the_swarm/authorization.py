from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$",
    ),
]
ResourceIdentifier = Annotated[
    str,
    Field(
        min_length=3,
        max_length=320,
        pattern=(
            r"^(workflow|workflow_version|agent|agent_version|capability|credential|trace):"
            r"[A-Za-z0-9][A-Za-z0-9._:/@-]*$"
        ),
    ),
]


class PrincipalKind(StrEnum):
    USER = "user"
    AGENT_VERSION = "agent_version"
    WORKFLOW_NODE = "workflow_node"
    RUNTIME = "runtime"


class Principal(BaseModel):
    """Stable identity of the actor requesting one protected action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PrincipalKind
    id: Identifier


class AuthorizationAction(StrEnum):
    WORKFLOW_READ = "workflow.read"
    WORKFLOW_EDIT = "workflow.edit"
    WORKFLOW_EXECUTE = "workflow.execute"
    AGENT_READ = "agent.read"
    AGENT_EDIT = "agent.edit"
    AGENT_EXECUTE = "agent.execute"
    CAPABILITY_READ = "capability.read"
    CAPABILITY_MANAGE = "capability.manage"
    CAPABILITY_INVOKE = "capability.invoke"
    CREDENTIAL_RESOLVE = "credential.resolve"
    TRACE_READ = "trace.read"


class AuthorizationContext(BaseModel):
    """Non-authorizing execution metadata used for future filtering and audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initiated_by: Principal | None = None
    workflow_version_id: UUID | None = None
    node_id: Identifier | None = None
    run_id: UUID | None = None
    runtime_id: Identifier | None = None


class AuthorizationRequest(BaseModel):
    """Typed request evaluated without framework, transport, or persistence concerns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: Principal
    action: AuthorizationAction
    resource: ResourceIdentifier
    context: AuthorizationContext = Field(default_factory=AuthorizationContext)


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


PolicyKey = tuple[Principal, AuthorizationAction, str]
PolicyValue = AuthorizationDecision | Iterable[AuthorizationDecision]


class PolicyEvaluator:
    """Evaluate exact policies with restrictive decisions taking precedence."""

    def __init__(self, policies: Mapping[PolicyKey, PolicyValue] | None = None) -> None:
        self._policies: dict[PolicyKey, tuple[AuthorizationDecision, ...]] = {}
        for key, configured in (policies or {}).items():
            decisions = (
                (configured,)
                if isinstance(configured, AuthorizationDecision)
                else tuple(configured)
            )
            if not decisions:
                raise ValueError("a policy must contain at least one decision")
            self._policies[key] = decisions

    def evaluate(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return the most restrictive exact match, or deny when no policy exists."""
        key = (request.principal, request.action, request.resource)
        decisions = self._policies.get(key, ())
        if AuthorizationDecision.DENY in decisions:
            return AuthorizationDecision.DENY
        if AuthorizationDecision.APPROVAL_REQUIRED in decisions:
            return AuthorizationDecision.APPROVAL_REQUIRED
        if AuthorizationDecision.ALLOW in decisions:
            return AuthorizationDecision.ALLOW
        return AuthorizationDecision.DENY


class PolicySource(Protocol):
    """Provide the exact policies applicable to one authorization request."""

    async def policies_for(self, request: AuthorizationRequest) -> Mapping[PolicyKey, PolicyValue]:
        """Return policies without broadening the request identity or resource."""


class EmptyPolicySource:
    """Default-deny source used until a persistent policy source is configured."""

    async def policies_for(self, request: AuthorizationRequest) -> Mapping[PolicyKey, PolicyValue]:
        return {}


class CompositePolicySource:
    """Combine independent exact policy sources without adding inheritance or wildcards."""

    def __init__(self, *sources: PolicySource) -> None:
        self.sources = sources

    async def policies_for(self, request: AuthorizationRequest) -> Mapping[PolicyKey, PolicyValue]:
        combined: dict[PolicyKey, list[AuthorizationDecision]] = {}
        for source in self.sources:
            for key, configured in (await source.policies_for(request)).items():
                decisions = (
                    (configured,)
                    if isinstance(configured, AuthorizationDecision)
                    else tuple(configured)
                )
                combined.setdefault(key, []).extend(decisions)
        return combined


class ConfiguredCapabilityPolicySource:
    """Expose exact capability allows supplied by trusted runtime configuration."""

    def __init__(self, grants: Mapping[str, Iterable[str]]) -> None:
        self._policies: dict[PolicyKey, AuthorizationDecision] = {}
        for principal_reference, capability_ids in grants.items():
            principal = self._parse_principal(principal_reference)
            for capability_id in capability_ids:
                request = AuthorizationRequest(
                    principal=principal,
                    action=AuthorizationAction.CAPABILITY_INVOKE,
                    resource=f"capability:{capability_id}",
                )
                self._policies[(request.principal, request.action, request.resource)] = (
                    AuthorizationDecision.ALLOW
                )

    async def policies_for(self, request: AuthorizationRequest) -> Mapping[PolicyKey, PolicyValue]:
        key = (request.principal, request.action, request.resource)
        decision = self._policies.get(key)
        return {} if decision is None else {key: decision}

    @staticmethod
    def _parse_principal(reference: str) -> Principal:
        kind, separator, identifier = reference.partition(":")
        if not separator or kind not in {
            PrincipalKind.AGENT_VERSION,
            PrincipalKind.WORKFLOW_NODE,
        }:
            raise ValueError(
                "capability policy principals must be agent_version or workflow_node references"
            )
        return Principal(kind=PrincipalKind(kind), id=identifier)


class DevelopmentPolicySource:
    """Process-local exact policies for one explicitly configured development user."""

    def __init__(self, user_id: str) -> None:
        self.principal = Principal(kind=PrincipalKind.USER, id=user_id)
        self._policies: dict[PolicyKey, AuthorizationDecision] = {}

    def grant_allow(
        self,
        action: AuthorizationAction,
        resource: str,
    ) -> None:
        """Register one exact development allow after validating its public contract."""
        request = AuthorizationRequest(
            principal=self.principal,
            action=action,
            resource=resource,
        )
        self._policies[(request.principal, request.action, request.resource)] = (
            AuthorizationDecision.ALLOW
        )

    async def policies_for(self, request: AuthorizationRequest) -> Mapping[PolicyKey, PolicyValue]:
        key = (request.principal, request.action, request.resource)
        decision = self._policies.get(key)
        return {} if decision is None else {key: decision}


class AuthorizationService:
    """Coordinate an injected policy source with the shared exact-match evaluator."""

    def __init__(self, policies: PolicySource) -> None:
        self.policies = policies

    async def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        policies = await self.policies.policies_for(request)
        return PolicyEvaluator(policies).evaluate(request)
