from uuid import uuid4

import pytest
from langchain_core.tools import tool

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationService,
    PolicyKey,
    PolicyValue,
    Principal,
    PrincipalKind,
)
from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizationError,
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)
from heart_of_the_swarm.tools import ToolRegistry, authorized_tool_view


class StaticPolicySource:
    def __init__(self, policies: dict[PolicyKey, PolicyValue]) -> None:
        self.policies = policies

    async def policies_for(self, request: AuthorizationRequest):
        return self.policies


def policy(
    principal: Principal,
    capability_id: str,
    decision: AuthorizationDecision,
) -> tuple[PolicyKey, PolicyValue]:
    return (
        (principal, AuthorizationAction.CAPABILITY_INVOKE, f"capability:{capability_id}"),
        decision,
    )


def authorizer(*policies: tuple[PolicyKey, PolicyValue]) -> CapabilityAuthorizer:
    return CapabilityAuthorizer(AuthorizationService(StaticPolicySource(dict(policies))))


def test_runtime_principals_use_immutable_version_and_node_identities() -> None:
    agent_version_id = uuid4()
    workflow_version_id = uuid4()

    assert CapabilityAuthorizer.agent_version_principal(agent_version_id) == Principal(
        kind=PrincipalKind.AGENT_VERSION,
        id=str(agent_version_id),
    )
    assert CapabilityAuthorizer.workflow_node_principal(
        workflow_version_id,
        "researcher",
    ) == Principal(
        kind=PrincipalKind.WORKFLOW_NODE,
        id=f"{workflow_version_id}/researcher",
    )


@pytest.mark.asyncio
async def test_tool_exposure_keeps_only_explicit_allows_in_declaration_order() -> None:
    principal = CapabilityAuthorizer.agent_version_principal(uuid4())
    service = authorizer(
        policy(principal, "web_search", AuthorizationDecision.ALLOW),
        policy(principal, "email_send", AuthorizationDecision.DENY),
        policy(principal, "github_read", AuthorizationDecision.APPROVAL_REQUIRED),
    )

    allowed = await service.allowed_capabilities(
        principal,
        ["web_search", "email_send", "missing", "github_read"],
    )

    assert allowed == ("web_search",)


@pytest.mark.asyncio
async def test_invocation_requires_an_exact_principal_and_capability_allow() -> None:
    allowed_principal = CapabilityAuthorizer.workflow_node_principal(uuid4(), "fetch")
    denied_principal = CapabilityAuthorizer.workflow_node_principal(uuid4(), "fetch")
    service = authorizer(
        policy(allowed_principal, "crm.read", AuthorizationDecision.ALLOW),
    )
    context = AuthorizationContext(node_id="fetch", run_id=uuid4())

    request = await service.require_invocation(
        allowed_principal,
        "crm.read",
        context=context,
    )

    assert request.resource == "capability:crm.read"
    assert request.context == context
    with pytest.raises(CapabilityAuthorizationError) as denied:
        await service.require_invocation(denied_principal, "crm.read", context=context)
    assert denied.value.decision == AuthorizationDecision.DENY


@pytest.mark.asyncio
async def test_approval_required_stops_before_invocation() -> None:
    principal = CapabilityAuthorizer.agent_version_principal(uuid4())
    service = authorizer(
        policy(principal, "remote.restart", AuthorizationDecision.APPROVAL_REQUIRED),
    )

    with pytest.raises(CapabilityAuthorizationError) as denied:
        await service.require_invocation(principal, "remote.restart")

    assert denied.value.decision == AuthorizationDecision.APPROVAL_REQUIRED


def test_capability_ids_are_validated_by_the_shared_resource_contract() -> None:
    principal = CapabilityAuthorizer.agent_version_principal(uuid4())

    with pytest.raises(ValueError):
        CapabilityAuthorizer.request(principal, "invalid capability")


@pytest.mark.asyncio
async def test_authorized_tool_views_are_isolated_and_recheck_before_invocation() -> None:
    calls: list[str] = []

    @tool
    async def web_search(query: str) -> str:
        """Search the web."""
        calls.append(query)
        return f"result:{query}"

    registry = ToolRegistry([web_search])
    allowed_principal = CapabilityAuthorizer.agent_version_principal(uuid4())
    denied_principal = CapabilityAuthorizer.agent_version_principal(uuid4())
    source = StaticPolicySource(
        dict([policy(allowed_principal, "web_search", AuthorizationDecision.ALLOW)])
    )
    service = CapabilityAuthorizer(AuthorizationService(source))
    allowed_security = ExecutionSecurityContext(
        principal=allowed_principal,
        authorization=AuthorizationContext(run_id=uuid4()),
    )
    denied_security = ExecutionSecurityContext(
        principal=denied_principal,
        authorization=AuthorizationContext(run_id=uuid4()),
    )

    allowed_view = await authorized_tool_view(
        registry,
        ["web_search"],
        service,
        allowed_security,
    )
    denied_view = await authorized_tool_view(
        registry,
        ["web_search"],
        service,
        denied_security,
    )

    assert [item.name for item in allowed_view] == ["web_search"]
    assert denied_view == []
    assert registry.names == ("web_search",)
    assert await allowed_view[0].ainvoke({"query": "first"}) == "result:first"

    source.policies.clear()
    with pytest.raises(CapabilityAuthorizationError):
        await allowed_view[0].ainvoke({"query": "blocked"})
    assert calls == ["first"]
