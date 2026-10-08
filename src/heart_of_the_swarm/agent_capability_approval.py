from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, NotRequired
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, PrivateStateAttr, ToolCallRequest
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from heart_of_the_swarm.approval_grants import (
    ApprovalGrantConsumer,
    CapabilityApprovalInterruptResponse,
    CapabilityApprovalRequest,
    CapabilityApprovalResume,
    fingerprint_capability_arguments,
)
from heart_of_the_swarm.authorization import AuthorizationDecision
from heart_of_the_swarm.capability_authorization import (
    CapabilityAuthorizationError,
    CapabilityAuthorizer,
    ExecutionSecurityContext,
)

_APPROVAL_GRANTS_KEY = "capability_approval_grants"


class _CapabilityApprovalState(AgentState[Any]):
    """Private grant bindings kept inside the checkpointed LangChain agent state."""

    capability_approval_grants: NotRequired[Annotated[dict[str, UUID], PrivateStateAttr]]


class AgentCapabilityApprovalMiddleware(AgentMiddleware[_CapabilityApprovalState, Any, Any]):
    """Pause one model-selected capability and consume its exact grant before execution."""

    state_schema = _CapabilityApprovalState

    def __init__(
        self,
        tools: Sequence[BaseTool],
        authorizer: CapabilityAuthorizer,
        security: ExecutionSecurityContext,
        grants: ApprovalGrantConsumer,
    ) -> None:
        super().__init__()
        self._tools_by_name = {tool.name: tool for tool in tools}
        self.authorizer = authorizer
        self.security = security
        self.grants = grants

    async def aafter_model(
        self,
        state: _CapabilityApprovalState,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """Interrupt after the model decision and before any selected tool executes."""
        del runtime
        message = next(
            (item for item in reversed(state["messages"]) if isinstance(item, AIMessage)),
            None,
        )
        if message is None or not message.tool_calls:
            return None

        normalized_calls: list[ToolCall] = []
        pending: list[tuple[ToolCall, CapabilityApprovalRequest]] = []
        for tool_call in message.tool_calls:
            normalized = self._normalize_tool_call(tool_call)
            normalized_calls.append(normalized)
            request, decision = await self.authorizer.invocation_decision(
                self.security.principal,
                normalized["name"],
                context=self.security.authorization,
            )
            if decision == AuthorizationDecision.DENY:
                raise CapabilityAuthorizationError(request, decision)
            if decision == AuthorizationDecision.APPROVAL_REQUIRED:
                if not normalized.get("id"):
                    raise ValueError("approval-gated tool calls require a stable call id")
                pending.append(
                    (
                        normalized,
                        CapabilityApprovalRequest(
                            authorization=request,
                            arguments_fingerprint=fingerprint_capability_arguments(
                                normalized["args"]
                            ),
                        ),
                    )
                )

        if not pending:
            return {"messages": [message.model_copy(update={"tool_calls": normalized_calls})]}
        if len(pending) != 1:
            raise RuntimeError(
                "one agent decision may request at most one approval-gated capability"
            )

        tool_call, approval_request = pending[0]
        response = interrupt(
            {
                "kind": "capability_approval",
                "node_id": self.security.authorization.node_id,
                "prompt": f"Allow {approval_request.authorization.resource} for this request?",
                "request": approval_request.model_dump(mode="json"),
            },
            response_schema=CapabilityApprovalInterruptResponse,
        )
        approval = CapabilityApprovalResume.model_validate(
            response.model_dump()
            if isinstance(response, CapabilityApprovalInterruptResponse)
            else response
        )
        if not approval.approved or approval.grant_id is None:
            raise CapabilityAuthorizationError(
                approval_request.authorization,
                AuthorizationDecision.APPROVAL_REQUIRED,
            )
        tool_call_id = tool_call["id"]
        return {
            "messages": [message.model_copy(update={"tool_calls": normalized_calls})],
            _APPROVAL_GRANTS_KEY: {tool_call_id: approval.grant_id},
        }

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Recheck policy and consume a matching grant immediately before the side effect."""
        authorization, decision = await self.authorizer.invocation_decision(
            self.security.principal,
            request.tool_call["name"],
            context=self.security.authorization,
        )
        if decision == AuthorizationDecision.DENY:
            raise CapabilityAuthorizationError(authorization, decision)
        if decision == AuthorizationDecision.APPROVAL_REQUIRED:
            tool_call_id = request.tool_call.get("id")
            grants = request.state.get(_APPROVAL_GRANTS_KEY) or {}
            grant_id = grants.get(tool_call_id) if tool_call_id else None
            if grant_id is None:
                raise CapabilityAuthorizationError(authorization, decision)
            await self.grants.consume(
                grant_id,
                authorization,
                fingerprint_capability_arguments(request.tool_call["args"]),
            )
        return await handler(request)

    def _normalize_tool_call(self, tool_call: ToolCall) -> ToolCall:
        """Validate model arguments once and bind the fingerprint to executed JSON values."""
        tool = self._tools_by_name.get(tool_call["name"])
        if tool is None:
            raise ValueError(f"agent selected unavailable tool '{tool_call['name']}'")
        arguments = tool.tool_call_schema.model_validate(tool_call["args"]).model_dump(mode="json")
        return {**tool_call, "args": arguments}
