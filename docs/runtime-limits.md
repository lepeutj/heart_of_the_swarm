# Runtime limits

This document defines the resource-limit boundaries for V2.6.7. It separates limits owned by one
logical execution from limits owned by the HTTP service or runtime process. It is not a generic
quota or billing system.

## Current inventory

| Concern | Current behavior | Gap |
| --- | --- | --- |
| Run timeout | `ExecutionPolicy.timeout_seconds` bounds one active workflow attempt. | Direct agent execution is not yet bounded by the same timeout. |
| Graph steps | LangGraph receives `ExecutionPolicy.recursion_limit`. | No separate limit is needed; this is the graph-step safety bound. |
| Subworkflows | `max_subworkflow_depth` is enforced. | None for the supported contract. |
| Supervisor routing | `max_handoffs` is enforced. | None for the supported contract. |
| Parallelism | One non-nested region, deterministic reducers, and conflict validation are enforced. | Nested or overlapping regions remain unsupported. |
| Worker concurrency | One worker process executes one claimed run at a time. | The value is implicit rather than configured and reported. |
| Standalone concurrency | Concurrent `/invoke` requests have no application-level bound. | Add a runtime semaphore and explicit overload response. |
| HTTP request size | `HTTPPolicy.max_request_bytes` rejects oversized bodies before routing in both APIs. | None for the current JSON HTTP boundary. |
| Workflow state size | Shared state and outputs are copied, checkpointed, and persisted without a size bound. | Validate the JSON state at stable boundaries before LangGraph persists it. |
| Model/tool calls | Callbacks trace calls and usage, but do not enforce budgets. | Add shared deterministic call counters. |
| Tokens/cost | Provider metadata is recorded when available. | Enforcement is provider-dependent and remains deferred. |
| Outbound network | MCP destinations are checked by the runtime `NetworkPolicy`. | Other remote adapters must adopt the same boundary when introduced. |

Authorization, approvals, network reachability, worker leases, and retry attempts are safety or
lifecycle controls. They do not replace resource limits.

## Policy ownership

### ExecutionPolicy

`ExecutionPolicy` limits one logical execution and travels with the execution contract:

```text
timeout_seconds
recursion_limit
max_subworkflow_depth
max_handoffs
max_model_calls       (planned)
max_tool_calls        (planned)
max_state_bytes       (planned)
```

`recursion_limit` remains the LangGraph graph-step limit; a second `max_graph_steps` field would
duplicate the same responsibility.

Timeout is measured per active `WorkflowRun` attempt. Human waiting time between an interruption
and its continuation does not consume it. Model- and tool-call counters apply across the complete
`ExecutionThread`, including continuation runs, so an interruption cannot reset a budget. A direct
agent run has no thread and owns its counters for that run.

Calls are counted when they start. A connector invocation counts as one tool call. Calls selected
inside LangChain agents use the same injected execution budget; subworkflows do not receive a new
budget. Exceeding a call budget fails before starting the next call.

`max_state_bytes` applies to the current public workflow shared state, measured as canonical UTF-8
JSON. It is checked on initial input and after node output mappings or reducer merges, before the
updated state is returned to LangGraph. It does not claim to bound internal framework checkpoint
metadata or cumulative event history.

### RuntimePolicy

`RuntimePolicy` protects one process or deployed runtime:

```text
max_concurrency
```

The worker must claim no more runs than its configured capacity. Its present capacity is one. The
standalone synchronous runtime must reject work when its semaphore is full instead of creating an
unbounded number of model/provider calls. CPU, memory, filesystem, and container placement remain
deployment/container concerns rather than fields in this policy.

### HTTPPolicy

`HTTPPolicy` protects network entry points:

```text
max_request_bytes
```

The limit applies before JSON decoding to mutating control-plane endpoints, webhooks, and the
standalone `/invoke` endpoint. A declared oversized `Content-Length` is rejected immediately. A
missing or false header does not bypass the limit: the received body is counted while reading.
Health checks and bodyless reads are unaffected.

## Failure semantics

- An oversized request returns `413 Payload Too Large` and creates no product record or run.
- A saturated synchronous runtime returns `503 Service Unavailable` with `Retry-After`; queued
  worker execution instead respects claim capacity and does not reject an already accepted run.
- An execution budget breach becomes a structured non-retryable run failure containing the limit
  name, configured value, and safe observed value.
- A state-size breach is detected before the oversized update is checkpointed or persisted.
- A model/tool call that already started is never cancelled solely because its counter reached the
  limit; the limit prevents the next call. The run timeout remains the bound for a hung call.
- Non-idempotent external effects are never retried automatically after a budget failure or crash.

## Implementation order

1. Make direct-agent timeout behavior match workflow attempts.
2. Make worker capacity explicit and bound standalone `/invoke` concurrency.
3. Enforce `ExecutionPolicy.max_state_bytes` at shared-state boundaries.
4. Add one thread-scoped counter for `max_model_calls` and `max_tool_calls` across connectors,
   agents, subworkflows, interruptions, and resume.

Token and monetary budgets, per-user quotas, distributed rate limiting, autoscaling, and a generic
quota administration system remain deferred until a measured product need exists.
