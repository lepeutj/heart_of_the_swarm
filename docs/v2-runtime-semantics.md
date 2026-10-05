# V2 runtime semantics

## Goal

V2 extends the acyclic V1 workflow into a stateful, durable, and composable agent graph without
reimplementing LangChain agent loops or LangGraph orchestration.

```text
WorkflowVersion
        ↓
ExecutionThread
        ↓ one or more
WorkflowRun
        ↓
LangGraph + checkpointer
        ↓
agents / connectors / subworkflows
```

This document defines the target contracts. Features become available only when their validation,
runtime adapter, persistence, observability, tests, and editor representation are complete.

## Non-negotiable boundaries

- `WorkflowSpec` remains JSON-serializable and contains no LangGraph objects, Python callables,
  credentials, or executable source.
- LangChain `create_agent` owns every autonomous model/tool loop.
- LangGraph owns traversal, checkpointing, routing, interrupts, streaming, and parallel scheduling.
- Heart of the Swarm owns declarations, validation, immutable versions, policies, lifecycle, and
  product metadata.
- An `AGENT` remains an opaque callable in the first V2 milestones. Exposing its internal graph is a
  later adapter, not a change to `AgentSpec`.
- Dynamic routes and reducers use closed allow-lists. A model cannot provide arbitrary node names,
  state paths, functions, or framework configuration.
- Checkpoints are technical execution state. They do not replace product runs, audit events, or
  MLflow traces.
- A successful external side effect must never be replayed automatically unless its idempotency
  contract is known.

## ExecutionPolicy

Execution limits are separate from container and network limits.

```yaml
execution_policy:
  timeout_seconds: 120
  recursion_limit: 100
```

Initial fields:

| Field | Meaning |
| --- | --- |
| `timeout_seconds` | Wall-clock limit for one run |
| `recursion_limit` | LangGraph recursion/step limit |

Rules:

- all values are positive and bounded by application-level maximums;
- deployment policy may lower an execution limit but never raise an application maximum;
- exhausting a limit produces a typed terminal or interrupted result, never an unstructured crash;
- model/tool-call and loop counters will be added only when they can share one reliable counter
  across nested agents and subworkflows;
- monetary budgets remain deferred until provider usage and pricing data are reliable.

`DeploymentPolicy` is a separate future contract for CPU, memory, filesystem, network, concurrency,
and allowed hosts.

## ExecutionThread and WorkflowRun

An `ExecutionThread` owns resumable context. A `WorkflowRun` records one invocation attempt in that
context.

```text
ExecutionThread #42
├── Run 1 → interrupted
├── Run 2 → failed
└── Run 3 → completed
```

Minimum thread record:

```yaml
id: uuid
workflow_version_id: uuid
status: active | interrupted | closed
created_at: timestamp
updated_at: timestamp
```

Minimum run additions:

```yaml
thread_id: uuid
attempt_index: integer
resumed_from_run_id: uuid | null
resume_checkpoint_id: string | null
```

Rules:

- a V2 thread pins one immutable `WorkflowVersion`;
- each initial invocation or resume creates a distinct run;
- run status remains the lifecycle of that invocation;
- closing a thread prevents new runs;
- a failed run does not silently discard or replay the thread state;
- conversation history is checkpointed agent state, not duplicated into generic workflow state;
- long-term memory is a later store and is not part of this contract.

### Thread and run creation ownership

The product creates thread identifiers. A caller never supplies a new arbitrary LangGraph
`thread_id`.

```text
initial invocation
→ RunCreationService creates ExecutionThread
→ RunCreationService creates WorkflowRun #1 atomically

resume request(thread_id)
→ ResumeService validates thread + checkpoint
→ ResumeService creates WorkflowRun #N
```

The product `ExecutionThread.id` maps one-to-one to LangGraph's configurable thread identifier. The
portable runner receives that identifier from its caller; it does not generate it or persist the
thread. A standalone runtime may generate an ephemeral product thread ID when no durable thread
adapter is configured.

### Resume semantics

A resume never changes the identity of an existing run. It creates a new run with:

- the same `thread_id`;
- the next monotonically increasing `attempt_index`;
- `resumed_from_run_id` referencing the previous run;
- `resume_checkpoint_id` referencing an explicit committed checkpoint;
- a new product trace ID and a new MLflow trace.

All traces carry `execution_thread_id`, run ID, attempt index, and resumed-from metadata so the UI
can reconstruct one continuous history without treating several execution periods as one trace.

## Checkpoint contract

The graph is compiled with an injected LangGraph checkpointer. The adapter maps
`ExecutionThread.id` to LangGraph's configurable thread identifier.

```text
product ExecutionThread.id
        ↓
LangGraph thread_id
        ↓
checkpoint sequence
```

Requirements:

- the worker and standalone runtime use the same runner-level checkpoint interface;
- PostgreSQL is the first durable checkpointer backend for the control plane;
- a resume loads an explicit checkpoint and creates a new `WorkflowRun`;
- checkpoint identifiers are stored as run references, not embedded checkpoint payloads;
- version, capability contracts, and execution policy are revalidated before resume;
- a worker crash may resume after the last committed checkpoint;
- a connector interrupted between an external side effect and checkpoint commit is not replayed
  automatically without an idempotency key or explicit operator decision.

The checkpointer is injected behind a runner-level interface. `WorkflowVersionRunner` and its
domain contracts must not import a PostgreSQL repository. The control-plane worker may inject a
PostgreSQL-backed implementation; a standalone runtime may inject another durable implementation
or explicitly run without resume support.

### Interruption and crash behavior

Explicit interruption and process failure remain different outcomes:

- an explicit LangGraph interrupt completes the current run as `interrupted` and keeps the thread
  `interrupted`;
- a crash or expired worker lease completes the current run as `failed` and keeps the last
  committed checkpoint available;
- neither outcome automatically starts another run;
- an operator or an explicit safe recovery policy requests resume from a selected checkpoint;
- a connector recorded as completed before that checkpoint is not executed again;
- an uncertain external side effect after the latest checkpoint requires explicit recovery rather
  than automatic replay.

Minimum product events:

```text
thread.created
workflow.queued
workflow.started
checkpoint.created
workflow.interrupted
workflow.failed
workflow.resumed
workflow.completed
```

`checkpoint.created` stores only checkpoint identity, node context, thread, run, and timestamp in
the product audit stream. The checkpoint payload remains owned by the LangGraph checkpointer.

## Controlled cycles

V2 does not add a `LOOP` node. A cycle is expressed by an existing conditional route whose back
edge declares a bounded loop contract.

Proposed public shape:

```yaml
edges:
  - source: review
    target: output
    condition:
      path: $.review.approved
      operator: equals
      value: true

  - source: review
    target: draft
    loop:
      id: revision
      max_iterations: 3
```

Validation requirements:

- every cycle contains exactly one declared loop back edge;
- removing declared back edges leaves an acyclic graph;
- the loop target reaches the loop source in the remaining graph;
- loop IDs are unique and iteration limits are positive and policy-bounded;
- only a condition node may choose between exit and loop routes in the first implementation;
- nested and overlapping loops are deferred;
- iteration counters use namespaced runtime state and cannot collide with workflow data.

Exceeding `max_iterations` produces `workflow.execution.iteration_limit` with workflow, node, loop,
thread, and run context.

## Subworkflow contract

V2 introduces `SUBWORKFLOW` as composition, not as serialized LangGraph state.

```yaml
- id: research
  type: subworkflow
  name: Research workflow
  config:
    workflow_version_id: 8134eb0e-7d3b-4dd5-8fde-28d8e64336b1
    inputs:
      request: {from_state: $.request}
    outputs:
      summary: {to_state: $.research.summary}
      sources: {to_state: $.research.sources}
```

Requirements:

- the node references an immutable `WorkflowVersion`;
- input and output mappings are validated against the child schemas;
- publishing freezes the referenced child version;
- recursive version references are rejected;
- nesting depth is policy-bounded;
- thread, run, trace, and execution counters propagate into the child;
- node events identify both the parent node and child workflow version;
- the portable runner receives a resolver; it never queries persistence directly;
- standalone export must bundle the transitive declarations or reject the version explicitly.

## Parallel branches and reducers

Multiple unconditional outgoing edges are enabled only with explicit parallel semantics. The first
implementation supports unconditional fan-out followed by an `all` join.

Concurrent writes to the same state path require a declared reducer:

```yaml
state_reducers:
  - path: $.research.results
    strategy: append
```

Initial closed reducer set:

| Strategy | Contract |
| --- | --- |
| `append` | Concatenate arrays in stable branch order |
| `merge_object` | Merge objects and reject duplicate keys |

Rules:

- concurrent writes without a reducer are rejected at publication;
- `replace` is not a valid concurrent reducer;
- reducers are declarative identifiers, never callbacks;
- reducer input and result types are checked against state schemas;
- product event sequencing must remain deterministic under concurrent node completion;
- conditional branches do not participate in an `all` join unless the join contract explicitly
  identifies the active branch set.

## Dynamic routing and handoffs

Explicit edges and `CONDITION` remain the default. Agent-directed routing is restricted to declared
targets:

```yaml
allowed_targets:
  - reviewer
  - researcher
```

The runtime validates the selected target and translates it internally to LangGraph `Command`.
`Command` is not serialized into `WorkflowSpec`.

The first handoff implementation must:

- use structured output rather than parse free text;
- reject unknown or unavailable targets;
- count the handoff as a graph step;
- preserve the shared thread and execution policy;
- emit a business routing event and normal LangGraph technical trace;
- define a deterministic fallback when no allowed target is selected.

Supervisor/router patterns build on this mechanism. A dedicated `HANDOFF` node is not planned.

## Interrupt, resume, and approval

Human approval is introduced only after durable checkpoints and threads work.

```text
node requests approval
→ LangGraph interrupt
→ thread becomes interrupted
→ run finishes as interrupted
→ user submits decision
→ new run resumes the same thread
```

Approval payloads have a declared JSON Schema. The API validates them before resume. Approval
cannot change the workflow version, policy ceiling, allowed tools, or routing allow-list.

## Streaming

Polling remains valid for short runs. Long executions use LangGraph streaming through a thin
adapter:

```text
LangGraph messages / updates / interrupts / subgraph events
        ↓
normalized runtime event
        ↓
SSE
        ↓
React run view
```

The product does not invent a second execution protocol. PostgreSQL keeps durable business events;
SSE delivery may be transient and reconnect from the last persisted event sequence.

## Security requirements carried through V2

Advanced orchestration does not wait for the complete remote-security milestone. Every new runtime
feature must preserve:

- immutable version references and capability contracts;
- external secret injection;
- allow-listed tools, MCP sources, reducers, and routing targets;
- request, state, event, and checkpoint size limits;
- safe public errors with trace identifiers;
- no arbitrary code, shell, templates, or callbacks in public specifications;
- explicit idempotency rules before retry or replay;
- authenticated APIs before any public-network deployment.

Remote runtime identity, secure MLflow transport, deployment credentials, image signing, and host
control belong to the later remote-execution and deployment milestones.

## Reference V2 scenario

The first complete V2 scenario is a checkpointed review loop:

```text
INPUT
→ Research agent
→ Review agent
→ CONDITION
   ├── approved → OUTPUT
   └── rejected → Research agent
```

Acceptance criteria:

- one immutable workflow version declares a loop bounded to three iterations;
- one execution thread owns all runs and checkpoints;
- an interrupt can pause after review and a new run can resume it;
- completed nodes before the checkpoint are not repeated;
- model calls, tool calls, graph steps, and iterations respect `ExecutionPolicy`;
- PostgreSQL exposes runs and business events while MLflow exposes nested technical spans;
- the editor displays the current iteration, interruption, resume, and final result;
- the same runner contract remains usable by the future standalone runtime.
