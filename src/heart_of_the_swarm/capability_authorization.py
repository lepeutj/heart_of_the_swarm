from collections.abc import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationService,
    Principal,
    PrincipalKind,
)
from heart_of_the_swarm.observability import audit_event


class ExecutionSecurityContext(BaseModel):
    """Trusted immutable identity and metadata for one runtime execution boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: Principal
    authorization: AuthorizationContext


class CapabilityAuthorizationError(PermissionError):
    """Safe runtime denial raised before a capability can produce side effects."""

    def __init__(
        self,
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
    ) -> None:
        self.request = request
        self.decision = decision
        super().__init__(f"capability invocation is {decision.value}: {request.resource}")


class CapabilityAuthorizer:
    """Apply the shared policy evaluator to immutable runtime capability identities."""

    def __init__(self, authorization: AuthorizationService) -> None:
        self.authorization = authorization

    @staticmethod
    def agent_version_principal(version_id: UUID | str) -> Principal:
        """Derive the principal used by one immutable saved-agent version."""
        return Principal(kind=PrincipalKind.AGENT_VERSION, id=str(version_id))

    @staticmethod
    def workflow_node_principal(
        workflow_version_id: UUID | str,
        node_id: str,
    ) -> Principal:
        """Derive the principal shared by inline agents, supervisors, and connectors."""
        return Principal(
            kind=PrincipalKind.WORKFLOW_NODE,
            id=f"{workflow_version_id}/{node_id}",
        )

    async def allowed_capabilities(
        self,
        principal: Principal,
        capability_ids: Iterable[str],
        *,
        context: AuthorizationContext | None = None,
    ) -> tuple[str, ...]:
        """Return requested capabilities with an explicit allow, preserving declaration order."""
        allowed: list[str] = []
        for capability_id in capability_ids:
            request = self.request(principal, capability_id, context=context)
            decision = await self.authorization.decide(request)
            self._audit(request, decision, boundary="exposure")
            if decision == AuthorizationDecision.ALLOW:
                allowed.append(capability_id)
        return tuple(allowed)

    async def require_invocation(
        self,
        principal: Principal,
        capability_id: str,
        *,
        context: AuthorizationContext | None = None,
    ) -> AuthorizationRequest:
        """Require an explicit allow immediately before one capability invocation."""
        request = self.request(principal, capability_id, context=context)
        decision = await self.authorization.decide(request)
        self._audit(request, decision, boundary="invocation")
        if decision != AuthorizationDecision.ALLOW:
            raise CapabilityAuthorizationError(request, decision)
        return request

    @staticmethod
    def request(
        principal: Principal,
        capability_id: str,
        *,
        context: AuthorizationContext | None = None,
    ) -> AuthorizationRequest:
        """Build the exact capability request used at exposure and invocation boundaries."""
        return AuthorizationRequest(
            principal=principal,
            action=AuthorizationAction.CAPABILITY_INVOKE,
            resource=f"capability:{capability_id}",
            context=context or AuthorizationContext(),
        )

    @staticmethod
    def _audit(
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
        *,
        boundary: str,
    ) -> None:
        """Record one safe runtime decision without capability arguments or workflow state."""
        context = request.context
        audit_event(
            "authorization.runtime_decision",
            principal_kind=request.principal.kind,
            principal_id=request.principal.id,
            action=request.action,
            resource=request.resource,
            decision=decision,
            boundary=boundary,
            run_id=str(context.run_id) if context.run_id is not None else None,
            workflow_version_id=(
                str(context.workflow_version_id)
                if context.workflow_version_id is not None
                else None
            ),
            node_id=context.node_id,
        )
