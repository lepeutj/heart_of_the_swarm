# Runtime capability authorization

This document defines V2.6d authorization for capabilities used by saved agents, inline agents,
supervisors, and deterministic connector nodes. It extends the shared authorization model without
changing `ToolRegistry` or introducing another capability catalogue.

## Runtime identities

The executing principal is derived only from immutable published records:

```text
direct saved-agent run        → agent_version:<agent_version_id>
saved agent in a workflow     → agent_version:<agent_version_id>
inline agent or supervisor    → workflow_node:<workflow_version_id>/<node_id>
connector                     → workflow_node:<workflow_version_id>/<node_id>
```

When a saved agent runs inside a workflow, the workflow version, node, run, and initiating user are
authorization context. They do not replace the saved agent's identity or increase its permissions.

Draft workflows and mutable agent IDs are not runtime principals. A runtime that cannot derive an
immutable principal must reject capability use.

## Protected request

Each capability check uses the existing authorization contract:

```text
principal
+ capability.invoke
+ capability:<capability_id>
+ execution context
→ allow | deny | approval_required
```

Capability IDs come from the trusted registry and must match the ID visible to LangChain. Tool
arguments, model output, workflow input, and client-provided identity fields never select the
principal or resource.

## Two enforcement points

Agent capabilities are checked twice for different reasons:

1. Before `create_agent`, only capabilities with an `allow` decision are exposed to the model.
2. Immediately before invocation, the same request is evaluated again.

Construction-time filtering prevents a model from seeing a denied tool. Invocation-time checking
protects against stale policy decisions, framework callbacks, or another path reaching the tool.
The second check is authoritative.

A connector has no model-facing exposure phase. It performs the invocation-time check immediately
before `ToolRegistry.invoke_one()`.

`deny` stops execution with a safe structured runtime error and no capability side effect.
`approval_required` also stops before invocation in V2.6d. V2.6f will translate that decision into
the durable human-approval flow and a request-bound grant.

## Policy source

Tool declaration and tool availability are not policies:

```text
AgentSpec.tools                 → requested capabilities
WorkflowVersion contracts      → required runtime compatibility
ToolRegistry                   → capabilities available in this process
PolicySource                   → capabilities the principal may invoke
```

The worker and standalone runtime must receive an explicit `PolicySource`. Missing policy remains
default deny. Development configuration may bootstrap exact allows for immutable principals, but
must do so as concrete policies through the same evaluator. There is no permissive runtime bypass.

The initial adapter reads exact allows from `CAPABILITY_POLICIES`:

```json
{
  "agent_version:VERSION_UUID": ["web_search"],
  "workflow_node:WORKFLOW_VERSION_UUID/fetch": ["database.read"]
}
```

This configuration is intentionally small: it supports only immutable agent-version and
workflow-node principals, exact capability IDs, and `allow`. Missing entries are denied. Restrictive
and approval policies remain available in the evaluator contract but require a future managed
policy source.

Persistent policy administration, roles, workspaces, and wildcard policy syntax remain deferred.

## Framework boundary

Heart of the Swarm owns principal derivation, policy evaluation, filtering, safe failures, and
audit metadata. LangChain continues to own the model/tool loop, and LangGraph continues to own
workflow routing.

```text
immutable execution context
→ authorized LangChain tool view
→ create_agent(...)
→ invocation guard
→ shared ToolRegistry capability
```

The authorized tool view is per execution. It must not mutate the shared registry because other
agents and concurrent runs can have different permissions.

## Audit

Each denied or approval-required check records the principal, action, capability resource,
decision, run ID, workflow version and node when present, and a safe reason code. It does not record
tokens, credentials, complete arguments, unrestricted workflow state, or hidden model reasoning.

## Acceptance criteria

- a saved agent derives its principal from the exact queued `AgentVersion`;
- inline agents, supervisors, and connectors derive principals from workflow version and node IDs;
- a denied tool is absent from the tools passed to `create_agent`;
- every actual capability invocation is authorized again;
- a denied connector produces no tool side effect;
- one principal can use a capability while another is denied;
- registry presence and `AgentSpec.tools` never grant permission by themselves;
- missing policy is denied;
- `approval_required` never invokes the capability in V2.6d;
- concurrent executions do not mutate or leak authorization through the shared registry;
- direct agents, workflows, subworkflows, resumed runs, and the standalone runtime use the same
  authorization boundary.

## Deferred work

V2.6d does not implement credentials, runtime identity, persistent policy management, wildcard
policies, roles, workspaces, approval grants, or network reachability. Those remain separate
milestones and independent gates.
