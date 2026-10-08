from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from heart_of_the_swarm.approval_grants import (
    ApprovalGrant,
    fingerprint_capability_arguments,
)
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationRequest,
    Principal,
    PrincipalKind,
)


def capability_request(run_id, principal, *, node_id="publish", resource="remote.publish"):
    return AuthorizationRequest(
        principal=principal,
        action=AuthorizationAction.CAPABILITY_INVOKE,
        resource=f"capability:{resource}",
        context=AuthorizationContext(run_id=run_id, node_id=node_id),
    )


def grant_for(request: AuthorizationRequest, fingerprint: str, **overrides) -> ApprovalGrant:
    values = {
        "id": uuid4(),
        "run_id": request.context.run_id,
        "node_id": request.context.node_id,
        "principal": request.principal,
        "action": request.action,
        "resource": request.resource,
        "arguments_fingerprint": fingerprint,
        "approved_by": Principal(kind=PrincipalKind.USER, id="operator-1"),
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(overrides)
    return ApprovalGrant.model_validate(values)


def test_argument_fingerprint_is_canonical_and_sensitive_to_values() -> None:
    first = fingerprint_capability_arguments(
        {"metadata": {"priority": 2, "labels": ["safe", "reviewed"]}, "task": "publish"}
    )
    reordered = fingerprint_capability_arguments(
        {"task": "publish", "metadata": {"labels": ["safe", "reviewed"], "priority": 2}}
    )
    changed = fingerprint_capability_arguments(
        {"task": "publish", "metadata": {"labels": ["safe"], "priority": 2}}
    )

    assert first == reordered
    assert first != changed


def test_argument_fingerprint_rejects_secrets_and_unsafe_values() -> None:
    with pytest.raises(ValueError, match="secret values"):
        fingerprint_capability_arguments({"token": SecretStr("private")})
    with pytest.raises(ValueError, match="non-finite"):
        fingerprint_capability_arguments({"score": float("nan")})
    with pytest.raises(ValueError, match="object keys"):
        fingerprint_capability_arguments({"nested": {1: "invalid"}})


def test_grant_matches_only_the_exact_unexpired_unconsumed_request() -> None:
    run_id = uuid4()
    principal = Principal(kind=PrincipalKind.WORKFLOW_NODE, id=f"{uuid4()}/publish")
    request = capability_request(run_id, principal)
    fingerprint = fingerprint_capability_arguments({"document_id": "doc-42"})
    grant = grant_for(request, fingerprint)

    assert grant.permits(request, fingerprint)
    assert not grant.permits(
        capability_request(run_id, principal, node_id="other"),
        fingerprint,
    )
    assert not grant.permits(request, fingerprint_capability_arguments({"document_id": "doc-43"}))
    assert not grant.permits(
        request,
        fingerprint,
        now=grant.expires_at,
    )
    assert not grant_for(
        request,
        fingerprint,
        consumed_at=datetime.now(UTC),
    ).permits(request, fingerprint)


def test_grant_rejects_non_user_approver_and_non_capability_action() -> None:
    run_id = uuid4()
    principal = Principal(kind=PrincipalKind.WORKFLOW_NODE, id=f"{uuid4()}/publish")
    request = capability_request(run_id, principal)
    fingerprint = fingerprint_capability_arguments({"document_id": "doc-42"})

    with pytest.raises(ValidationError, match="user approver"):
        grant_for(
            request,
            fingerprint,
            approved_by=Principal(kind=PrincipalKind.RUNTIME, id="runtime-1"),
        )
    with pytest.raises(ValidationError, match="only capability invocation"):
        grant_for(request, fingerprint, action=AuthorizationAction.WORKFLOW_EXECUTE)


def test_grant_requires_run_and_node_context_to_match() -> None:
    principal = Principal(kind=PrincipalKind.AGENT_VERSION, id=str(uuid4()))
    request = capability_request(uuid4(), principal)
    fingerprint = fingerprint_capability_arguments({"query": "status"})
    grant = grant_for(request, fingerprint)
    contextless = AuthorizationRequest(
        principal=principal,
        action=AuthorizationAction.CAPABILITY_INVOKE,
        resource=request.resource,
    )

    assert not grant.permits(contextless, fingerprint)
