# V2 runtime semantics

This is the compact V2 contract. Implemented features are marked explicitly; later sections are
design constraints, not claims of runtime support.

## Boundaries

- Specs contain JSON data only: no LangGraph objects, callables, credentials, or source code.
- LangChain `create_agent` owns autonomous model/tool loops.
- LangGraph owns traversal, checkpoints, routing, interrupts, streaming, and parallel scheduling.
- The product owns declarations, validation, versions, policy, lifecycle, and business metadata.
- Checkpoints, product runs, and MLflow traces are related but never substitute for one another.
- Successful external side effects are not replayed automatically without a known idempotency
  contract.

## V2.1 — Durable execution (implemented)

### Execution policy

```yaml
execution_policy:
  timeout_seconds: 120
  recursion_limit: 100
```

Both values are positive and application-bounded. Exhaustion produces a structured execution
failure. CPU, memory, network, filesystem, and placement belong to a future deployment policy.

### Thread, run, and checkpoint

```text
ExecutionThread
├── WorkflowRun #1 → interrupted or failed
└── WorkflowRun #2 → resumed → completed
```

- `ExecutionThread` pins one immutable `WorkflowVersion` and owns logical continuity.
- `WorkflowRun` is one bounded attempt with `attempt_index`, `resumed_from_run_id`, and optional
  `resume_checkpoint_id`.
- The product creates the thread ID and passes it to LangGraph as `thread_id`.
- Resume creates a new run and MLflow trace in the same thread; an old run is never reopened.
- The selected checkpoint must be the latest checkpoint in the same thread and workflow version.
- The checkpointer is injected into `WorkflowVersionRunner`; the runner does not load repositories.
- SQLite and PostgreSQL use official LangGraph checkpointer implementations.
- A crash never starts an automatic replay. Explicit resume uses a committed checkpoint.

Minimum audit events are `thread.created`, `workflow.queued`, `workflow.started`,
`checkpoint.created`, `workflow.interrupted`, `workflow.failed`, `workflow.resumed`, and
`workflow.completed`. Product audit stores checkpoint references, not checkpoint payloads.

## V2.2a — One bounded back edge (implemented)

There is no `LOOP` node. A condition selects an ordinary edge that may declare a bound:

```yaml
- source: review
  target: output
  condition: {path: $.approved, operator: equals, value: true}

- source: review
  target: draft
  loop: {id: revision, max_iterations: 3}
```

Rules:

- schema v1 rejects loop metadata; schema v2 accepts it;
- the current runtime accepts exactly one loop edge;
- the source is a condition and each condition route has a distinct target;
- removing the loop edge leaves a DAG;
- the target reaches the source through forward edges, so the edge closes a real cycle;
- the positive iteration bound is at most 100;
- the counter lives in namespaced internal state and persists in LangGraph checkpoints;
- every node visit emits its own lifecycle events;
- exceeding the bound raises `workflow.execution.iteration_limit`.

Nested and overlapping loops are deferred.

## V2.2b — Immutable subworkflow (implemented)

```yaml
- id: research
  type: subworkflow
  config:
    workflow_version_id: 8134eb0e-7d3b-4dd5-8fde-28d8e64336b1
    inputs:
      request: {from_state: $.request}
    outputs:
      summary: {to_state: $.research.summary}
```

Runtime contract:

- reference only immutable `WorkflowVersion` records;
- validate mappings against parent and child schemas;
- retain the exact referenced child version ID when publishing the parent;
- reject direct and indirect recursive dependencies and bound nesting depth;
- propagate thread/run context, execution counters, events, and trace correlation;
- namespace child state and checkpoints; never serialize a compiled child graph.

The child is compiled as a native LangGraph subgraph and remains in the parent's `WorkflowRun` and
`ExecutionThread`. Event `execution_path` values are contextual (for example,
`root/research/review`) and never enter business state. Resume uses the latest selected checkpoint
without creating a LangGraph time-travel fork, so a completed child connector is not replayed.
Standalone artifacts reject subworkflow dependencies until an export can bundle the complete
immutable dependency tree.

## V2.3+ deferred contracts

### Parallel state

Use LangGraph fan-out/fan-in. Each concurrent state path must declare one closed reducer strategy
such as replace, append-list, merge-object-without-conflicts, or numeric-sum. Reject ambiguous
writes. Event order must use sequence data, not timestamps alone.

### Dynamic routing and handoffs

Agents may select only validated `allowed_targets`. Translate valid selections to LangGraph
`Command` internally. Do not expose arbitrary node IDs, framework configuration, or a public
`HANDOFF` node until the opaque-agent model proves insufficient.

### Human interaction

Use LangGraph interrupts over the durable checkpoint contract. Persist an interruption record with
node, schema, prompt, checkpoint, and expiry. Validate the resume payload before creating a new run.

### Streaming

Adapt LangGraph streaming to stable SSE events for messages, updates, interrupts, custom progress,
and subgraph paths. Reconnection must use sequence IDs and must not reconstruct state by replaying
side effects.

### Security and remote execution

Authenticate API and runtime identities; authorize immutable versions and capabilities; inject
credentials through references; bound requests, concurrency, state, and execution; restrict SSRF
and egress; redact safe errors; and secure result, heartbeat, and MLflow transport. Prefer outbound
runtime communication behind NAT.

## Reference V2 scenario

```text
INPUT → AGENT draft → AGENT review
                     ├── approved → SUBWORKFLOW publish → OUTPUT
                     └── retry (max 3) → draft
```

Completion requires a checkpointed bounded loop, one immutable child workflow, correlated events
and traces, and no custom agent loop or graph traversal.
