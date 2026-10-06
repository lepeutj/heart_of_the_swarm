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

## V2.3 — Parallel state (implemented)

### Graph semantics

Parallelism uses ordinary graph edges and native LangGraph scheduling. There is no `PARALLEL` node:

```text
        ┌→ B ─┐
A ──────┤     ├→ D
        └→ C ─┘
```

- multiple unconditional outgoing edges from a non-condition node create a fan-out;
- a condition remains an exclusive ordered route, never an implicit fan-out;
- a node reached by branches from the same unconditional fan-out is an `all` fan-in and runs once
  after all required branches complete;
- convergence after exclusive condition routes remains an `any selected predecessor` dependency and
  must not wait for routes that were not selected;
- the compiler groups the incoming dependencies when adding the LangGraph edge;
- the first runtime supports one non-nested, non-overlapping parallel region;
- a bounded loop may exist outside that region but cannot cross or execute inside it;
- a parallel branch may contain an immutable `SUBWORKFLOW`.

### Shared state contract

`WorkflowSpec.state_schema` is an optional map keyed by canonical state path. Each entry contains a
JSON Schema and one reducer from the closed set `replace`, `append`, or `merge_dict`:

```yaml
state_schema:
  $.research_results:
    schema: {type: array, items: {type: object}}
    reducer: append
  $.metadata:
    schema: {type: object}
    reducer: merge_dict
  $.final_answer:
    schema: {type: string}
    reducer: replace
```

`replace` is the default and permits only one writer in a parallel region. `append` concatenates
list contributions in declared branch order. `merge_dict` performs a shallow merge and rejects a
key produced by more than one branch. Concurrent ancestor/descendant writes, such as `$.result` and
`$.result.title`, are conflicting and rejected; a reducer applies only to its exact path.

Static write analysis covers agent, connector, subworkflow, transform, and supported legacy output
mappings. Writes to distinct paths are valid. Writes to the same or overlapping path require an
exact `append` or `merge_dict` declaration. Nodes return state updates for LangGraph reduction; the
product does not implement traversal or concurrent scheduling.

### Determinism, events, and resume

Execution completion order is intentionally not deterministic. Merged state is deterministic by
the outgoing edge order declared at the fan-out. Every branch event carries `execution_path`,
`node_id`, a stable contextual `branch_id`, persistence `sequence`, and `timestamp`. Sequence records
observation order only; it does not claim a logical ordering between branches.

If one branch fails, the run fails. If one branch completes and a sibling interrupts, the checkpoint
must retain the completed contribution. Explicit resume continues the interrupted branch, does not
replay the completed sibling, and releases the fan-in only after every required contribution exists.

### Acceptance scenarios

1. Fan-out starts two independent branches.
2. Fan-in waits for both required predecessors and runs once.
3. Concurrent writes to distinct paths succeed.
4. The same or overlapping path without a combinatory reducer is rejected.
5. `append` combines branch lists in declared branch order.
6. `merge_dict` combines objects with distinct keys.
7. Duplicate `merge_dict` keys are rejected.
8. One failed branch fails the workflow.
9. Resume does not replay a completed sibling when another branch interrupted.
10. A `SUBWORKFLOW` executes inside one parallel branch.
11. A bounded loop outside the parallel region still works.
12. Events identify branches and preserve an unambiguous observation sequence.

Partial joins, quorum joins, first-result-wins, nested parallel regions, arbitrary reducers, and
parallel loops remain deferred.

## V2.4a — Single-target supervisor routing

### Decision contract

The supervisor produces only a strict routing decision. It produces business output only when it
finishes:

```yaml
# Continue through one target.
action: handoff
target: researcher
task: Find primary sources for the requested topic.

# Finish the supervisor cycle.
action: finish
result: {summary: Research and review are complete.}
```

`SupervisorDecision` is a discriminated union with these invariants:

- `handoff` requires a non-empty `target` and `task`, forbids `result`, and accepts exactly one
  target;
- `finish` requires a JSON-serializable `result` and forbids `target` and `task`;
- `target` must be one of the supervisor's validated `allowed_targets`;
- every allowed target is a different local `AGENT` node; the supervisor itself, `SUBWORKFLOW`, and
  arbitrary node targets are rejected;
- model prose outside this structure is invalid rather than interpreted as routing.

### Control and data flow

```text
             ┌→ Researcher ─┐
Supervisor ──┼→ Analyst ────┼→ Supervisor
             └→ Reviewer ───┘
                    │
Supervisor finish ──┴→ OUTPUT
```

- a handoff is a temporary transfer of control, not a tool call or a new `WorkflowRun`;
- the selected target receives the decision `task` plus only its declared state input mappings;
- target outputs return through declared output mappings; the complete workflow state is never
  inherited implicitly;
- completing a target returns control to the supervisor; a target failure fails the workflow rather
  than becoming an implicit supervisor decision, and direct target-to-target handoff is rejected;
- `max_handoffs` is a positive, application-bounded execution-policy value and counts transfers
  from the supervisor, not node visits or returns;
- valid decisions translate to LangGraph `Command` internally. Specifications never contain
  LangGraph objects and expose no public `HANDOFF` node.

### Checkpoints and events

A completed target is a committed execution step. If a checkpoint is written after the target and
the process crashes before the supervisor runs again, resume continues at the supervisor and does
not invoke the target again.

Minimum contextual events are `supervisor.decision`, `handoff.started`, `handoff.completed`,
`handoff.failed`, and `handoff.limit_reached`. They carry the supervisor and target node IDs,
`handoff_index`, execution path, run ID, and thread ID. The decision payload must not expose hidden
model reasoning.

### Normative scenarios

1. `Supervisor → Researcher → Supervisor → Reviewer → Supervisor → finish → OUTPUT` succeeds.
2. A target outside `allowed_targets` is rejected before it executes.
3. `handoff` without a target or task is rejected.
4. `finish` with target or task fields is rejected.
5. A target receives only the task and explicitly mapped inputs.
6. A target's mapped outputs are available to the next supervisor decision.
7. The same allowed target may be selected more than once while the bound remains available.
8. Exceeding `max_handoffs` produces a structured execution failure.
9. Target completion returns to the supervisor; target failure fails the workflow.
10. Resume after a completed target continues at the supervisor without replaying the target.
11. Every decision and transfer emits contextual events with a monotonic handoff index.
12. Multiple targets, subworkflow targets, and direct target-to-target handoffs are rejected.

Multiple selection, dynamic fan-out, quorum, first-N completion, cancellation, agent-as-tool,
human handoff, target subworkflows, multiple supervisors, and supervisors inside parallel regions
remain deferred.

## V2.5a — Durable human approval contract

Status: static contract, durable interruption, application-level response/resume, and thin HTTP
adapters implemented. React can edit the node, display the pending decision, submit approve/reject,
and follow the resumed run. V2.5a is complete.

`HUMAN_APPROVAL` is the first deliberately narrow human-interaction node. It asks one boolean
question and delegates suspension and resume to LangGraph:

```yaml
- id: approve_publication
  type: human_approval
  config:
    prompt: Approve publication of the reviewed report?
    output: {to_state: $.publication.approved}
```

The node type is intentionally specific. A neutral persisted `WorkflowInterruption` supports later
text, choice, or form interactions without making their runtime contracts part of V2.5a:

```text
WorkflowInterruption
├── id, workflow_version_id, thread_id, run_id, node_id
├── kind = approval
├── prompt and response_schema
├── checkpoint_id
├── status = pending | resolved | cancelled
├── response
└── created_at, resolved_at
```

The runtime sequence is:

```text
WorkflowRun R1 reaches HUMAN_APPROVAL
→ LangGraph interrupt() creates resumable checkpoint state
→ checkpoint reference and pending interruption are persisted
→ R1 becomes interrupted; the worker returns to the queue

operator submits {approved: true | false}
→ validate response and atomically claim the pending interruption
→ create WorkflowRun R2 in the same ExecutionThread
→ R2.resumed_from_run_id = R1 and R2.resume_checkpoint_id is recorded
→ resume with LangGraph Command(resume=response)
→ write approval result through the declared output mapping
→ continue the graph without replaying completed nodes
```

Invariants:

- one pending interruption references one exact resumable checkpoint;
- a response is accepted at most once, including under concurrent requests;
- an invalid response changes neither the interruption nor the execution thread and creates no run;
- resolving an already resolved or cancelled interruption is rejected;
- every accepted response creates a new `WorkflowRun` in the same `ExecutionThread` and a new
  MLflow trace;
- no worker or HTTP request remains blocked while waiting for an operator;
- resume does not replay nodes committed before the interruption;
- `pending`, `resolved`, and `cancelled` are the complete V2.5a lifecycle; expiry is deferred;
- checkpoint payloads remain owned by the injected LangGraph checkpointer, not the product table.

Minimum audit events are `workflow.interrupted`, `approval.requested`, `approval.resolved`,
`approval.cancelled`, and `workflow.resumed`. Events and traces may include the validated approval
value but never hidden model reasoning or checkpoint payloads.

Normative scenarios:

1. An approval node interrupts, persists one pending record, and terminates the current run.
2. `{approved: true}` creates a new run and follows the approved continuation.
3. `{approved: false}` creates a new run and follows the rejected continuation.
4. Invalid, duplicate, cancelled, foreign-thread, or foreign-checkpoint responses create no run.
5. Resume retains the same thread, increments the attempt index, and does not replay prior nodes.
6. A worker restart while approval is pending does not lose or consume the interruption.

Free-text input, multiple choice, forms, expiry, delegation, human handoffs, bulk approvals, and UI
notifications remain deferred. V2.5a also does not add streaming.

## V2.5b+ deferred contracts

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
