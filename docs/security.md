# Security contracts

This document defines the V2.6 security boundary. It specifies authorization semantics before any
JWT, OAuth, remote-runtime, or credential implementation is selected.

## Core model

Authorization answers one question:

```text
Principal → requests Action → on Resource
```

`AuthorizationContext` describes where and why the request occurs. It supports filtering, audit,
and contextual policy, but it never grants permissions or replaces the principal's identity.

The initial principal kinds are closed:

```text
user
agent_version
workflow_node
runtime
```

- A saved agent executes as its immutable `AgentVersion` principal.
- An inline agent executes as `workflow_version + node_id` and therefore uses a `workflow_node`
  principal.
- A deterministic `CONNECTOR` also uses its published `workflow_node` principal.
- A runtime principal identifies one trusted execution environment. It does not inherit the user,
  workflow, or agent permissions.
- Tools, connectors, capabilities, MCP servers, providers, and external services are resources or
  implementation details, never additional product principals.

Identifiers must be derived from authenticated users or immutable published records, never from
LLM output, workflow input, tool arguments, or arbitrary client claims.

## Actions and resources

Actions use a closed product vocabulary. The initial vocabulary is:

```text
workflow.read
workflow.edit
workflow.execute
agent.read
agent.edit
agent.execute
capability.read
capability.manage
capability.invoke
credential.resolve
trace.read
```

Resources identify the protected object and its immutable version where execution depends on one:

```text
workflow:<workflow_id>
workflow_version:<version_id>
agent:<agent_id>
agent_version:<version_id>
capability:<capability_id>
credential:<credential_ref>
trace:<trace_id>
```

`AGENT` and `CONNECTOR` differ in how a capability is selected, but use the same authorization
mechanism when that capability is invoked. An agent lets the model select from its exposed tools;
a connector declares exactly one invocation in the workflow.

## Request and decision

The V2.6a code contract will remain small and JSON-serializable:

```text
Principal
AuthorizationContext
AuthorizationRequest
AuthorizationDecision
PolicyEvaluator
```

Decision values are closed:

```text
allow
deny
approval_required
```

Unknown principals, actions, resources, policies, and missing context are denied. An absent policy
is a denial. Context cannot turn a denied principal into an allowed one.

Useful context fields include `initiated_by`, `workflow_version_id`, `node_id`, `run_id`, and
`runtime_id`. They preserve the execution chain without creating synthetic identities. When an
`AgentVersion` is reused by multiple workflows, the agent remains the executing principal while
the invoking workflow and node stay in context.

## Enforcement points

Authorization must be checked at the boundary that performs the protected action:

1. The API checks the authenticated user before reading, editing, or executing product objects.
2. Run creation checks access to the exact immutable execution version.
3. Agent construction exposes only capabilities allowed for the executing agent principal.
4. Capability invocation checks again immediately before a tool, connector, or MCP call. Filtering
   tools at construction is not sufficient enforcement.
5. The credential resolver checks the runtime principal before resolving a `CredentialRef`.
6. Trace and result endpoints check their read permissions independently from execution rights.

Capability presence in `ToolRegistry`, declaration in an `AgentSpec`, or availability of a
credential never implies authorization.

The effective invocation requires every independent boundary to pass:

```text
initiating user may execute the immutable version
AND executing agent/workflow node may invoke the capability
AND runtime provides the declared capability contract
AND runtime may resolve the required credential reference
AND the external credential permits the remote operation
```

## Approval grants

`approval_required` suspends execution through the existing durable human-interruption mechanism.
An accepted approval creates an ephemeral grant for exactly one protected request; it does not add
a role or reusable permission.

```text
ApprovalGrant
├── run_id
├── node_id
├── principal
├── action
├── resource
├── arguments_fingerprint
├── approved_by
└── expires_at
```

The grant allows execution only when it is unresolved, unexpired, belongs to the current run and
node, and matches the principal, action, resource, and canonical argument fingerprint. Otherwise
the decision is `deny`. A grant is consumed at most once.

The argument fingerprint must be computed by trusted product code from the exact validated request.
Raw credentials and secret values are never fingerprint inputs, approval payloads, audit fields, or
public specification data.

## Audit contract

Authorization audit events record the principal reference, action, resource, decision, policy or
reason code, run context, and trace correlation. They do not record credentials, tokens, complete
tool arguments, unrestricted workflow state, or hidden model reasoning.

Denials exposed through HTTP or runtime errors use safe messages. Detailed internal reasons remain
in protected audit data.

## Normative scenarios

1. A user allowed to execute a workflow cannot make an agent inherit the user's broader capability
   permissions.
2. A connector node denied `capability.invoke` cannot call the capability even when it exists in the
   registry and its credential is configured.
3. An agent denied a capability never receives it in `create_agent()` and is denied again if an
   invocation bypasses construction-time filtering.
4. An inline agent and connector use their immutable workflow-node identity, not a client-supplied
   node or workflow identifier.
5. A runtime that may resolve a credential cannot invoke the protected capability unless the
   executing agent or workflow node is also allowed.
6. Missing policy, unknown resource, stale version, mismatched context, or unavailable credential
   produces `deny` without executing an external side effect.
7. An approval grant for one run, node, capability, or argument fingerprint cannot authorize a
   different request and cannot be consumed twice.
8. Authorization denials are correlated with product audit and MLflow trace references without
   exposing secrets.

## V2.6a scope

V2.6a defines and tests the pure authorization value objects and evaluator boundary. It does not
add JWT validation, OAuth, user management, policy persistence, remote-runtime authentication,
credential storage, or a general-purpose IAM language. Those adapters follow only after this
contract is implemented and verified.

V2.6b user authentication is defined separately in `authentication.md`. It converts a validated
HTTP bearer credential into a user principal without placing roles or permissions in the token.
V2.6c user HTTP authorization is defined in `user-authorization.md`. It maps authenticated control
plane requests to exact actions and resources through an injected policy source.
V2.6d runtime capability authorization is defined in `capability-authorization.md`. It derives
immutable agent and workflow-node principals, filters model-visible tools, and rechecks every
capability immediately before invocation.

Outbound destination controls are defined separately in `network-policy.md`. Network permission is
a runtime/deployment constraint and never follows from user or capability authorization.
