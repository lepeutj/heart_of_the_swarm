from uuid import UUID

import pytest
from pydantic import ValidationError

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationRequest,
    PolicyEvaluator,
    Principal,
    PrincipalKind,
)

CAPABILITY = "capability:crm.customer.read"


def principal(kind: PrincipalKind, identifier: str = "principal-1") -> Principal:
    return Principal(kind=kind, id=identifier)


def request_for(
    actor: Principal,
    *,
    resource: str = CAPABILITY,
    context: AuthorizationContext | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        principal=actor,
        action=AuthorizationAction.CAPABILITY_INVOKE,
        resource=resource,
        context=context or AuthorizationContext(),
    )


def policies_for(
    request: AuthorizationRequest,
    *decisions: AuthorizationDecision,
):
    return {(request.principal, request.action, request.resource): decisions}


def test_principal_requires_a_known_kind_and_safe_identifier() -> None:
    assert principal(PrincipalKind.AGENT_VERSION).kind == PrincipalKind.AGENT_VERSION

    with pytest.raises(ValidationError):
        Principal(kind="connector", id="connector-1")
    with pytest.raises(ValidationError):
        Principal(kind="user", id=" unsafe ")


def test_unknown_actions_and_resource_kinds_are_rejected() -> None:
    actor = principal(PrincipalKind.USER)

    with pytest.raises(ValidationError):
        AuthorizationRequest(
            principal=actor,
            action="capability.delete",
            resource=CAPABILITY,
        )
    with pytest.raises(ValidationError):
        AuthorizationRequest(
            principal=actor,
            action=AuthorizationAction.CAPABILITY_INVOKE,
            resource="connector:crm",
        )


def test_missing_policy_denies_by_default() -> None:
    assert PolicyEvaluator().evaluate(request_for(principal(PrincipalKind.USER))) == "deny"


def test_explicit_allow_is_returned() -> None:
    request = request_for(principal(PrincipalKind.AGENT_VERSION, "agent-version-1"))
    evaluator = PolicyEvaluator(policies_for(request, AuthorizationDecision.ALLOW))

    assert evaluator.evaluate(request) == AuthorizationDecision.ALLOW


def test_explicit_deny_takes_priority_over_other_decisions() -> None:
    request = request_for(principal(PrincipalKind.WORKFLOW_NODE, "version-1/node:fetch"))
    evaluator = PolicyEvaluator(
        policies_for(
            request,
            AuthorizationDecision.ALLOW,
            AuthorizationDecision.APPROVAL_REQUIRED,
            AuthorizationDecision.DENY,
        )
    )

    assert evaluator.evaluate(request) == AuthorizationDecision.DENY


def test_approval_required_takes_priority_over_allow() -> None:
    request = request_for(principal(PrincipalKind.WORKFLOW_NODE, "version-1/node:send"))
    evaluator = PolicyEvaluator(
        policies_for(
            request,
            AuthorizationDecision.ALLOW,
            AuthorizationDecision.APPROVAL_REQUIRED,
        )
    )

    assert evaluator.evaluate(request) == AuthorizationDecision.APPROVAL_REQUIRED


def test_context_cannot_grant_permission_without_a_matching_policy() -> None:
    context = AuthorizationContext(
        initiated_by=principal(PrincipalKind.USER, "owner-1"),
        workflow_version_id=UUID("11850612-0402-418e-bee3-f4603a72d4eb"),
        node_id="researcher",
        run_id=UUID("293ce849-ef4a-468b-ae41-21c92bcdd10e"),
        runtime_id="deployment-42",
    )
    request = request_for(
        principal(PrincipalKind.AGENT_VERSION, "agent-version-1"),
        context=context,
    )

    assert PolicyEvaluator().evaluate(request) == AuthorizationDecision.DENY


def test_agent_version_and_workflow_node_are_distinct_principals() -> None:
    saved_agent = request_for(principal(PrincipalKind.AGENT_VERSION, "agent-version-1"))
    inline_agent = request_for(
        principal(PrincipalKind.WORKFLOW_NODE, "workflow-version-1/node:researcher")
    )
    evaluator = PolicyEvaluator(policies_for(saved_agent, AuthorizationDecision.ALLOW))

    assert evaluator.evaluate(saved_agent) == AuthorizationDecision.ALLOW
    assert evaluator.evaluate(inline_agent) == AuthorizationDecision.DENY


def test_same_capability_can_differ_by_principal() -> None:
    reader = request_for(principal(PrincipalKind.AGENT_VERSION, "reader-v1"))
    writer = request_for(principal(PrincipalKind.AGENT_VERSION, "writer-v1"))
    policies = {
        **policies_for(reader, AuthorizationDecision.ALLOW),
        **policies_for(writer, AuthorizationDecision.DENY),
    }
    evaluator = PolicyEvaluator(policies)

    assert evaluator.evaluate(reader) == AuthorizationDecision.ALLOW
    assert evaluator.evaluate(writer) == AuthorizationDecision.DENY


def test_empty_policy_decision_set_is_rejected() -> None:
    request = request_for(principal(PrincipalKind.RUNTIME, "runtime-1"))

    with pytest.raises(ValueError, match="at least one decision"):
        PolicyEvaluator(policies_for(request))
