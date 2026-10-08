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
run_id
node_id
principal
action
resource
arguments_fingerprint
approved_by
expires_at
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

## Argument fingerprint

Trusted runtime code computes SHA-256 over canonical JSON-compatible validated capability
arguments. Object keys are sorted, array order is preserved, non-finite numbers and unsupported
runtime objects are rejected, and no raw arguments are persisted with the grant.

Injected credentials and `SecretStr` values are forbidden inputs. Capability adapters must attach
runtime credentials after authorization and fingerprinting, as the MCP bearer adapter already
does. The fingerprint is audit correlation, not encryption for secrets supplied as tool arguments.

## Persistence and events

The future durable adapter will link a grant to the capability interruption that created it. It
must create the continuation run and grant in one transaction, then atomically consume the grant
before invocation.

Safe product events may include grant ID, capability resource, decision, and fingerprint. They
must not contain capability arguments, credentials, workflow state, or hidden model reasoning.

## V2.6f increments

1. Pure `ApprovalGrant` and canonical fingerprint contract.
2. Durable grant persistence linked to capability interruptions.
3. LangGraph interrupt/resume integration for workflow connectors and agent tools.
4. Thin API and React reuse of the existing approval interaction.

Multi-approver rules, delegation, quorum, long-lived grants, wildcard grants, and standalone
approval brokers are deferred.
