# User HTTP authorization

This document defines the V2.6c authorization boundary for authenticated control-plane requests.
Authentication establishes a `Principal(kind="user")`; it never grants access by itself.

## Request pipeline

```text
HTTP request
→ authenticate bearer credential
→ Principal(user)
→ load the protected resource identity when necessary
→ build AuthorizationRequest
→ AuthorizationService
→ allow or reject with 403
→ execute endpoint
```

Missing or invalid authentication returns `401`. A valid identity denied by policy returns `403`.
The protected operation must not run before an `allow` decision.

## Policy boundary

`AuthorizationService` only coordinates the existing contracts:

```text
Principal + action + resource
→ PolicySource
→ exact policies
→ PolicyEvaluator
→ AuthorizationDecision
```

`PolicySource` is injectable and may perform asynchronous I/O. V2.6c provides only an in-memory
`DevelopmentPolicySource`; a persistent implementation is deferred. The development source contains
explicit exact policies and uses the same evaluator as every future source. It is not an
authentication or authorization bypass.

At API startup, the development bootstrap expands the configured development user's local access
into exact policies for resources already present in the repositories. An authorized create or
publish operation adds exact policies for the newly created object or version. These policies are
process-local and are reconstructed after restart. No wildcard, role, workspace inheritance, or
client-provided policy is supported.

Collection resources authorize operations that do not yet have an object identifier. They are
ordinary exact resources, not wildcards:

```text
workflow:collection
workflow:catalog
workflow:validation
workflow:triggers
workflow:interruptions
agent:collection
agent:catalog
agent:builder
agent:validation
capability:catalog
trace:usage
```

V2.6c adds `capability.read` and `capability.manage` to protect configuration catalogues without
misusing `capability.invoke`. Invocation remains a later runtime enforcement point.

## HTTP permission matrix

Where a row says "resolved", the adapter must load the record first, derive the immutable resource
identifier from trusted persistence, authorize it, and only then expose or mutate it. Client input
never defines the protected identity by itself.

| HTTP operation | Action | Resource |
| --- | --- | --- |
| Read providers, models, tools, skills, or workflow capabilities | `capability.read` | `capability:catalog` |
| Add a skill or MCP server | `capability.manage` | `capability:catalog` |
| Test or refresh an MCP server | `capability.manage` | `capability:mcp:<server_id>` |
| Validate a workflow declaration | `workflow.edit` | `workflow:validation` |
| List workflow summaries | `workflow.read` | `workflow:catalog` |
| Create a workflow draft | `workflow.edit` | `workflow:collection` |
| Read or edit an existing workflow | `workflow.read` / `workflow.edit` | `workflow:<workflow_id>` |
| Publish a workflow version | `workflow.edit` | `workflow:<workflow_id>` |
| Read the latest workflow version | `workflow.read` | `workflow:<workflow_id>` |
| Queue or resume a workflow version | `workflow.execute` | `workflow_version:<version_id>` |
| Read a workflow run result | `workflow.read` | resolved `workflow_version:<version_id>` |
| Read or stream workflow run events | `trace.read` | resolved `trace:<trace_id>` |
| List all workflow interruptions | `workflow.read` | `workflow:interruptions` |
| Read one interruption | `workflow.read` | resolved `workflow_version:<version_id>` |
| Respond to an interruption | `workflow.execute` | resolved `workflow_version:<version_id>` |
| Create a trigger | `workflow.edit` | resolved `workflow_version:<target_version_id>` |
| List triggers | `workflow.read` | `workflow:triggers` |
| Read or invoke a trigger | `workflow.read` / `workflow.execute` | resolved `workflow_version:<target_version_id>` |
| Design or validate an agent declaration | `agent.edit` | `agent:builder` / `agent:validation` |
| Create an agent | `agent.edit` | `agent:collection` |
| List agent summaries | `agent.read` | `agent:catalog` |
| Read or version an existing agent | `agent.read` / `agent.edit` | `agent:<agent_id>` |
| Queue an agent run | `agent.execute` | resolved `agent_version:<version_id>` |
| Read or cancel an agent run | `agent.read` / `agent.execute` | resolved `agent_version:<version_id>` |
| List runs for one agent | `agent.read` | `agent:<agent_id>` |
| Read agent run events or trajectory | `trace.read` | resolved `trace:<trace_id>` |
| Read aggregated usage | `trace.read` | `trace:usage` |

Public health, readiness, OpenAPI, and static UI routes remain outside this matrix.

## Enforcement invariants

- Authorization executes after authentication and before the protected operation.
- A lookup needed to identify a resource may occur before authorization, but it exposes no product
  data beyond a safe `404` policy.
- Missing policies and `approval_required` both deny HTTP execution in V2.6c. Approval integration
  remains V2.6f.
- Listing permission is explicit and does not imply access to every object detail endpoint.
- Permission on a mutable draft does not imply execution permission on a published version.
- Permission on a run result does not imply permission to read its technical trace.
- One user's exact resource policy cannot authorize another user or another resource.
- Denial events contain principal, action, resource, decision, trace ID, and a safe reason code;
  they never contain bearer tokens, unrestricted payloads, or hidden model reasoning.

## V2.6c acceptance criteria

```text
authenticated user A + exact policy for workflow A → allow
authenticated user A + no policy for workflow B    → 403
authenticated user B + user A policy               → 403
missing or invalid bearer credential                → 401
denied mutation or execution                        → no side effect and no queued run
denied trace read                                    → no events or trajectory returned
development mode                                    → same PolicyEvaluator path
```

V2.6c does not add persistent users, policy administration, RBAC, workspaces, wildcard policies,
OAuth, capability invocation enforcement, runtime identities, credentials, or approval grants.
