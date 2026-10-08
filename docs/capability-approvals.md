# Request-bound capability approval

This document defines V2.6f authorization grants created when a capability policy returns
`approval_required`. It extends the existing durable human-interruption mechanism without turning
an approval into a role or reusable permission.

## Boundary

```text
capability.invoke request
→ policy returns approval_required
→ durable workflow interruption
→ operator approves or rejects
→ one exact ApprovalGrant
→ resumed invocation consumes the grant once
```

An explicit `HUMAN_APPROVAL` node remains a workflow decision. A capability approval is generated
by the authorization boundary immediately before an external side effect. Both use the same
durable interruption lifecycle, but only the latter creates an `ApprovalGrant`.

Direct agent runs and standalone runtimes do not yet have the durable workflow checkpoint needed
for this flow. In those runtimes, `approval_required` remains a safe denial until an equivalent
interrupt/resume contract exists.

## Exact grant

An `ApprovalGrant` contains only trusted identifiers and a hash:

```text
id
interruption_id
source_run_id
continuation_run_id
node_id
principal
action
resource
arguments_fingerprint
approved_by
status: active | consumed
created_at
expires_at
consumed_by_run_id
consumed_at
```

It permits an invocation only when all of the following still match:

- current continuation run;
- current workflow node;
- immutable executing principal;
- exact action and resource;
- canonical argument fingerprint;
- unexpired lifetime;
- not previously consumed.

Changing one argument, capability, principal, node, or run requires a new approval. Denial and
rejection create no grant. Consumption must be atomic with the decision that releases the external
side effect.

An approval grant is not a distributed transaction. A crash before consumption leaves the grant
active and no external call has started. A crash after consumption may leave the external effect
unknown; the runtime must not retry automatically. Capability-specific idempotency keys may be
added later where an external system supports them.

Authorization is always re-evaluated before grant lookup. A current `deny` takes precedence over
an existing grant. A grant can satisfy only a current `approval_required` decision; it never
bypasses or replaces normal policy evaluation.

## Argument fingerprint

Trusted runtime code computes SHA-256 over canonical JSON-compatible validated capability
arguments. Object keys are sorted, array order is preserved, non-finite numbers and unsupported
runtime objects are rejected, and no raw arguments are persisted with the grant.

Injected credentials and `SecretStr` values are forbidden inputs. Capability adapters must attach
runtime credentials after authorization and fingerprinting, as the MCP bearer adapter already
does. The fingerprint is audit correlation, not encryption for secrets supplied as tool arguments.

## Persistence and events

The durable adapter links each grant to the capability interruption that created it. Resolution,
continuation-run creation, grant creation, thread transition, and lifecycle events share one
database transaction. A conditional update atomically consumes the exact active, unexpired grant
for the continuation run before invocation.

Safe product events may include grant ID, capability resource, decision, and fingerprint. They
must not contain capability arguments, credentials, workflow state, or hidden model reasoning.

## V2.6f increments

1. Pure `ApprovalGrant` and canonical fingerprint contract.
2. Durable grant persistence linked to capability interruptions. **Implemented.**
3. LangGraph interrupt/resume integration for sequential deterministic workflow connectors.
   **Implemented.**
4. Thin API and React reuse of the existing approval interaction. **Implemented through the shared
   interruption endpoints and run panel.**

Sequential root-workflow agents use LangChain middleware after the model call and before tool
execution. The agent graph inherits the parent LangGraph checkpointer, so the selected tool call and
its arguments survive interruption and resume without replaying the model decision. The resumed
tool wrapper rechecks policy and consumes the exact grant immediately before invocation.

One model turn may request at most one approval-gated capability in this increment. A turn that
requests several approval-gated calls fails before any selected tool runs because the current
product interruption creates one exact grant. Direct agent runs and standalone runtimes still deny
`approval_required` because they have no durable product interruption lifecycle.

Approval-required agent tools or connectors inside parallel regions or nested subworkflows remain
denied until their interruption namespaces and branch-resume semantics are explicitly validated.
Sequential root-workflow nodes are the V2.6f runtime contract.

Multi-approver rules, delegation, quorum, long-lived grants, wildcard grants, and standalone
approval brokers are deferred.
