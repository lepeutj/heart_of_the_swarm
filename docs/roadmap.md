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

Status: typed state declarations and static parallel validation implemented; runtime not implemented.

1. Infer one non-nested fan-out/fan-in region from ordinary unconditional edges; add no public
   `PARALLEL` node. **Static analysis implemented.**
2. Compile convergence from an unconditional fan-out as an `all` dependency; preserve `any selected
   predecessor` convergence for exclusive condition routes.
3. Add optional path-keyed `state_schema` declarations with the closed reducer set `replace`,
   `append`, and `merge_dict`. **Implemented.**
4. Reject concurrent writes to the same or overlapping paths unless the exact shared path has a
   combinatory reducer. `replace` never resolves a concurrent conflict. **Implemented.**
5. Keep merged state deterministic by declared branch order and reject duplicate `merge_dict` keys.
6. Persist contextual branch identity and observation sequence without imposing a fake execution
   order.
7. Resume an interrupted branch without replaying a completed sibling.
8. Add the React representation only after runtime and checkpoint behavior pass integration tests.

Exit criterion: two agents can work concurrently, append typed results, and feed one synthesis node
with deterministic state and events. V2.3 initially excludes nested or overlapping parallel regions,
loops inside a parallel region, partial joins, quorum joins, and first-result-wins behavior.

## V2.4 — Multi-agent routing

1. Add validated `allowed_targets` for agent-directed routing.
2. Translate valid selections to LangGraph `Command` internally.
3. Add supervisor/router and delegation patterns without a public `HANDOFF` node.
4. Evaluate agent-as-subgraph only after opaque agent execution remains stable with checkpoints.

Exit criterion: one router agent can hand work to one declared agent and return to an explicit
workflow route without selecting arbitrary nodes.

## V2.5 — Interaction

1. Add durable interrupts and resume payload validation.
2. Add human approval over the same checkpoint contract.
3. Adapt LangGraph streaming to SSE for messages, updates, interrupts, and subgraphs.
4. Extend React run observation without creating UI-only execution concepts.

Exit criterion: an operator can observe, approve, and resume a long-running workflow without
restarting it.

## V2.6 — Security and remote execution

1. Add API/runtime identity and authorization.
2. Apply execution, request, concurrency, network, and state-size limits.
3. Add credential references for MCP and providers without storing secrets in specs.
4. Secure remote result, heartbeat, and MLflow trace transport.
5. Prefer outbound runtime communication when the remote host is behind NAT.

Basic secret handling, allow-lists, safe errors, SSRF protection, and execution bounds are
cross-cutting requirements and must not wait for this milestone.

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
