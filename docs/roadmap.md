# Roadmap

## Product flow

```text
Build visually or with the Builder LLM
→ validate
→ save draft
→ create immutable version
→ test and trace
→ deploy
```

## Completed foundation

- Shared `AgentSpec`, LangChain `create_agent`, OpenAI/OpenRouter providers, and tool registry.
- Immutable agent versions, worker runs, trajectories, usage metrics, and Docker stack.
- `WorkflowSpec` validation and direct LangGraph `StateGraph` translation.
- React Flow editor with typed inspectors, edge insertion, detachment, and ordered conditions.
- Unified `AGENT` nodes backed by inline `AgentSpec` data or immutable `AgentVersion` references.
- Markdown Skills injected into agent context and deterministic registered connectors.
- Mutable workflow drafts with separate layout metadata and immutable workflow versions.
- Dynamic capability registration and MCP discovery through the shared tool registry.
- Real MCP-to-connector execution, structured result normalization, isolated server failures, and
  schema-driven connector arguments.
- Persisted MCP HTTP sources with add/test/refresh UI, provider-safe namespacing, and immutable
  capability contracts on published workflow versions.
- Durable workflow-version runs through the worker, ordered node lifecycle events, bounded
  LangGraph execution, and editor polling/result display.
- Shared synchronous workflow-input validation and a persistence-independent
  `WorkflowVersionRunner` for already-loaded immutable versions.
- Typed manual, webhook, and interval-schedule trigger contracts persisted against immutable agent
  or workflow versions.
- External workflow webhooks that create validated durable runs and preserve trigger origin.
- Exported workflow-version artifacts and a generic standalone workflow runtime API.
- Optional MLflow LangChain/LangGraph autotracing with runtime trace-ID correlation.
- Real Docker verification of an exported workflow artifact and its nested MLflow trace.

## V2 objective

Express and deploy stateful, durable, and composable agent graphs while continuing to delegate
agent loops to LangChain and orchestration to LangGraph. The normative runtime contracts are in
[`v2-runtime-semantics.md`](v2-runtime-semantics.md).

## V2.1 — Runtime semantics

Status: complete.

1. Introduce `ExecutionPolicy` independently from future deployment limits.
2. Persist `ExecutionThread` separately from `WorkflowRun`.
3. Add an injected durable LangGraph checkpointer.
4. Create a new run for each initial invocation or resume in the same thread.
5. Keep checkpoint state, product lifecycle, and MLflow traces separate.

Exit criterion: a sequential workflow can stop after a checkpoint and resume in a new run without
repeating completed nodes. The original run remains terminal, the new run references it and the
selected checkpoint, and both traces share the product thread identifier.

## V2.2 — Controlled composition

1. V2.2a: support one bounded conditional back edge without adding a `LOOP` node. **Implemented.**
2. V2.2b: add immutable `SUBWORKFLOW` references with typed input/output mappings. **Implemented.**
3. Reject recursive subworkflow dependencies and bound nesting depth. **Implemented.**
4. Preserve checkpoint state, trace context, and hierarchical events across nested workflows.
   **Implemented.**

Exit criterion: the reference research/review workflow can loop at most three times and compose one
versioned child workflow.

Standalone artifact bundling for a subworkflow dependency tree is deferred; unsupported exports
are rejected explicitly rather than producing incomplete runtimes.

## V2.3 — Parallel state

Status: implemented; one non-nested parallel region is supported by the runtime and editor.

1. Infer one non-nested fan-out/fan-in region from ordinary unconditional edges; add no public
   `PARALLEL` node. **Static analysis implemented.**
2. Compile convergence from an unconditional fan-out as an `all` dependency; preserve `any selected
   predecessor` convergence for exclusive condition routes. **Implemented.**
3. Add optional path-keyed `state_schema` declarations with the closed reducer set `replace`,
   `append`, and `merge_dict`. **Implemented.**
4. Reject concurrent writes to the same or overlapping paths unless the exact shared path has a
   combinatory reducer. `replace` never resolves a concurrent conflict. **Implemented.**
5. Keep merged state deterministic by declared branch order and reject duplicate `merge_dict` keys.
   **Implemented.**
6. Persist contextual branch identity and observation sequence without imposing a fake execution
   order. **Implemented.**
7. Resume an interrupted branch without replaying a completed sibling. **Implemented.**
8. Add the React representation only after runtime and checkpoint behavior pass integration tests.
   **Implemented.**

Exit criterion: two agents can work concurrently, append typed results, and feed one synthesis node
with deterministic state and events. V2.3 initially excludes nested or overlapping parallel regions,
loops inside a parallel region, partial joins, quorum joins, and first-result-wins behavior.

## V2.4 — Multi-agent routing

Status: V2.4a runtime and V2.4b typed React editing implemented.

1. Define a strict `SupervisorDecision`: one `handoff` target and task, or one final `finish`
   result. **Implemented as a typed contract.**
2. Validate `allowed_targets` as local `AGENT` node IDs and keep handoff state boundaries explicit.
   **Static validation implemented.**
3. Add `max_handoffs` to execution policy; count transfers rather than supervisor/target node visits.
   **Implemented.**
4. Translate valid single-target selections to LangGraph `Command` internally. **Implemented.**
5. Require every target to return control to the supervisor before another decision. **Implemented.**
6. Preserve the completed target across checkpoint resume before returning to the supervisor.
   **Implemented.**
7. Add contextual decision and handoff events without a public `HANDOFF` node. **Implemented.**
8. Edit the exact supervisor contract in React while keeping allowed targets as configuration
   relations rather than workflow edges. **Implemented.**

Exit criterion: a supervisor can route sequentially to a researcher and reviewer, regain control
after each target, then finish. It cannot select arbitrary nodes, exceed its handoff limit, inherit
the complete workflow state implicitly, or replay a completed target after resume.

Multiple targets per decision, `SUBWORKFLOW` targets, agent-as-tool, direct target-to-target
handoffs, quorum, cancellation, and human handoffs remain deferred.

## V2.5 — Interaction

V2.5a human approval semantics are documented. Its typed node, LangGraph interruption boundary,
durable checkpoint correlation, pending interruption persistence, and application-level response
and resume service are implemented. Thin HTTP read/response adapters and React support are also
implemented: the typed editor and run panel support pending approval, approve/reject, and
continuation-run tracking.

1. Add the typed `HUMAN_APPROVAL` node and neutral persisted `WorkflowInterruption` lifecycle.
   **Implemented as a static contract and atomic persistence boundary.**
2. Interrupt through LangGraph, persist the exact checkpoint reference, and release the worker.
   **Implemented.**
3. Validate and atomically accept one boolean response before creating a resumed run in the same
   execution thread. **Implemented at the application-service boundary.**
4. Expose pending/resolved/cancelled approval state through the API and React run observation.
   **Implemented for pending approval and response; historical state remains available through the
   API.**
5. V2.5b-1: expose durable run events through reconnectable SSE and replace React polling.
   **Implemented and validated through Docker.**
6. V2.5b-2: adapt native LangGraph messages, updates, custom progress, and subgraph streams only
   after selecting an explicit live transport between workers and API processes.

Exit criterion: an operator can observe, approve, and resume a long-running workflow without
restarting it. **Achieved for V2.5a.**

V2.5b-1 streams the product events already persisted by the worker. It does not store
model tokens or raw LangGraph state in PostgreSQL. Native token/message streaming remains a separate
increment because the API and worker are different processes and no live event broker is currently
part of the architecture.

## V2.6 — Security and remote execution

1. V2.6a: implement the closed principal/action/resource contract, default-deny evaluator boundary,
   authorization checkpoints, and request-bound approval grants defined in `docs/security.md`.
   **Pure value objects and exact-match policy evaluation are implemented; runtime enforcement is
   intentionally deferred to the following increments.**
2. V2.6b: authenticate users and protect control-plane API operations through the development and
   JWT provider contract in `docs/authentication.md`. **Implemented and validated through Docker.**
3. V2.6c: enforce exact user policies on control-plane HTTP operations as defined in
   `docs/user-authorization.md`. **Implemented; Docker smoke validation remains.**
4. V2.6d: enforce agent-version and workflow-node capability policies at tool exposure and
   invocation boundaries. **Implemented through exact configured policies, isolated per-run tool
   views, invocation guards, and safe audit decisions. Managed policy persistence is deferred.**
5. V2.6e: authenticate runtime instances and resolve credential references without storing secrets
   in specs.
6. V2.6f: integrate `approval_required` decisions with durable human approval.
7. Apply execution, request, concurrency, network, and state-size limits.
8. Secure remote result, heartbeat, and MLflow trace transport; prefer outbound runtime
   communication when the remote host is behind NAT.

Basic secret handling, allow-lists, safe errors, SSRF protection, and execution bounds are
cross-cutting requirements and must not wait for this milestone.

The outbound destination contract is defined in `docs/network-policy.md`. Its implementation is
required before remote deployment, but it must remain independent from capability authorization and
must not block explicitly configured private MCP deployments.

Exit criterion: one authenticated remote runtime can receive work, report lifecycle and results,
and emit correlated traces without exposing an unauthenticated administration port.

## V2.7 — Deployment control plane

1. Define deployment and deployment-revision contracts.
2. Publish one generic hardened runtime image.
3. Transfer and verify immutable artifacts.
4. support one local Docker target, then one Docker/VPS or Raspberry Pi target;
5. add health, start, stop, restart, and rollback lifecycle.

Exit criterion: a selected immutable version can be deployed, invoked, observed, and rolled back on
one remote Docker host.

## Deferred until demanded by a completed path

- long-term memory and vector stores;
- arbitrary reducer plugins;
- arbitrary Python or shell tools;
- Kubernetes and additional cloud providers;
- generic OAuth providers and multi-tenant billing;
- automatic retries of non-idempotent external side effects.

Do not add UI-only execution concepts, custom agent loops, custom workflow traversal, or duplicate
tracing/evaluation platforms.
