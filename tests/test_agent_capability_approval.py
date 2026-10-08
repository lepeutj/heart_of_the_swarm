from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from heart_of_the_swarm.agent_capability_approval import AgentCapabilityApprovalMiddleware
from heart_of_the_swarm.approval_grants import ApprovalGrant
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationService,
    PolicyKey,
    PolicyValue,
)
from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizationError,
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)


class StaticPolicySource:
    def __init__(self, policies: dict[PolicyKey, PolicyValue]) -> None:
        self.policies = policies

    async def policies_for(self, _request: AuthorizationRequest):
        return self.policies


class RecordingGrantConsumer:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def consume(
        self,
        grant_id: UUID,
        _request: AuthorizationRequest,
        _arguments_fingerprint: str,
    ) -> ApprovalGrant:
        self.calls.append(grant_id)
        raise AssertionError("the grant must not be consumed")


@tool
async def publish(value: str) -> str:
    """Publish one value."""
    return value


def middleware(
    decision: AuthorizationDecision,
) -> tuple[
    AgentCapabilityApprovalMiddleware,
    RecordingGrantConsumer,
]:
    principal = CapabilityAuthorizer.workflow_node_principal(uuid4(), "publisher")
    key = (principal, AuthorizationAction.CAPABILITY_INVOKE, "capability:publish")
    authorizer = CapabilityAuthorizer(AuthorizationService(StaticPolicySource({key: decision})))
    grants = RecordingGrantConsumer()
    return (
        AgentCapabilityApprovalMiddleware(
            [publish],
            authorizer,
            ExecutionSecurityContext(
                principal=principal,
                authorization=AuthorizationContext(run_id=uuid4(), node_id="publisher"),
            ),
            grants,
        ),
        grants,
    )


async def test_approval_rejects_unstable_tool_call_before_interrupt() -> None:
    approval, grants = middleware(AuthorizationDecision.APPROVAL_REQUIRED)
    message = AIMessage(
        content="",
        tool_calls=[{"name": "publish", "args": {"value": "report"}, "id": ""}],
    )

    with pytest.raises(ValueError, match="stable call id"):
        await approval.aafter_model({"messages": [message]}, None)  # type: ignore[arg-type]

    assert grants.calls == []


async def test_deny_overrides_checkpointed_grant_before_tool_execution() -> None:
    approval, grants = middleware(AuthorizationDecision.DENY)
    grant_id = uuid4()
    side_effects: list[str] = []
    request = SimpleNamespace(
        tool_call={
            "name": "publish",
            "args": {"value": "sensitive-report"},
            "id": "publish-call-1",
        },
        state={"capability_approval_grants": {"publish-call-1": grant_id}},
    )

    async def execute(_request) -> ToolMessage:
        side_effects.append("executed")
        return ToolMessage(content="done", tool_call_id="publish-call-1")

    with pytest.raises(CapabilityAuthorizationError) as denied:
        await approval.awrap_tool_call(request, execute)  # type: ignore[arg-type]

    assert denied.value.decision == AuthorizationDecision.DENY
    assert grants.calls == []
    assert side_effects == []
