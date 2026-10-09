# Runtime limits

This document defines the resource-limit boundaries for V2.6.7. It separates limits owned by one
logical execution from limits owned by HTTP entry points, model capabilities, and future
control-plane scheduling. It is not a generic quota or billing system.

## Current inventory

| Concern | Current behavior | Gap |
| --- | --- | --- |
| Run timeout | Workflow attempts and direct persisted agent runs both bound active execution. | Standalone invocation policy remains a runtime concern. |
| Graph steps | LangGraph receives `ExecutionPolicy.recursion_limit`. | No separate limit is needed; this is the graph-step safety bound. |
| Subworkflows | `max_subworkflow_depth` is enforced. | None for the supported contract. |
| Supervisor routing | `max_handoffs` is enforced. | None for the supported contract. |
| Parallelism | One non-nested region, deterministic reducers, and conflict validation are enforced. | Nested or overlapping regions remain unsupported. |
| Scheduling concurrency | The local worker executes one run at a time. The deployment target is one run per container. | Control-plane scheduling remains deferred. |
| HTTP request size | `HTTPPolicy.max_request_bytes` rejects oversized bodies before routing in both APIs. | None for the current JSON HTTP boundary. |
| Workflow state size | Initial and propagated shared state are bounded before run persistence or LangGraph checkpointing. | None for the public shared-state boundary. |
| Model calls | A callback reserves calls atomically against the durable execution thread before provider invocation. | None for workflow execution. |
| Tool calls | Connectors and agent-selected tools reserve calls atomically against the durable execution thread before capability execution. | None for workflow execution. |
| Model context | `ModelDescriptor.context_length` can describe the selected model, and OpenRouter supplies it when available. | Enforce a per-invocation input budget in the model runtime. |
| Tokens/cost | Provider metadata is recorded when available. | Thread-wide token and monetary enforcement remains deferred. |
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
max_model_calls
max_tool_calls
max_state_bytes
```

`recursion_limit` remains the LangGraph graph-step limit; a second `max_graph_steps` field would
duplicate the same responsibility.

Timeout is measured per active `WorkflowRun` attempt. Human waiting time between an interruption
and its continuation does not consume it. Model- and tool-call counters apply across the complete
`ExecutionThread`, including continuation runs, so an interruption cannot reset a budget. A direct
agent run has no thread and does not yet enforce call counters. It remains bounded by its
configurable ten-minute timeout; longer work belongs in durable workflows with checkpoints and
resume rather than one worker-bound invocation.

Model calls are reserved atomically when LangChain starts the model callback, before provider
execution. Continuation runs reload the same durable thread counter, while subworkflows receive the
same injected budget. Exceeding the budget fails before starting the next provider call.

Tool arguments are validated and capability authorization or approval is resolved before one tool
call is reserved. Reservation happens immediately before capability execution, so rejected or
invalid requests do not consume budget, while an attempted capability that later fails does.
Connectors and tools selected inside LangChain agents share the same thread counter, including
through subworkflows and continuation runs.

Durable threads persist consumed model and tool calls, but the configured maxima are read by each
runtime attempt rather than snapshotted on the thread. Changing runtime configuration between an
interruption and its continuation can therefore change the remaining allowance.

Parallel branches share one atomic budget. When fewer calls remain than concurrently runnable
branches request, the first observed reservation succeeds; the counter remains correct, but the
winning branch is intentionally not deterministic. Per-branch quotas are not part of this contract.

`max_state_bytes` applies to the current public workflow shared state, measured as canonical UTF-8
JSON. It is checked on initial input and after node output mappings or reducer merges, before the
updated state is returned to LangGraph. It does not claim to bound internal framework checkpoint
metadata or cumulative event history.

### Per-model input budget

The model context window is a capability of the model selected for one invocation, not an
`ExecutionPolicy` limit. A workflow may use models with different context windows, so each call is
checked against the descriptor resolved by the model registry/provider.

```text
available_input_tokens
= context_window_tokens
- reserved_output_tokens
- safety_margin_tokens
```

`ModelInputBudget` owns `reserved_output_tokens` and `safety_margin_tokens`. The invocation check
must count everything actually sent: system instructions, skills, messages, mapped inputs, tool
definitions, and structured-output schemas. An overflow fails explicitly with
`model.context_budget_exceeded`; the runtime never truncates, summarizes, or drops context
silently. Token counting may be provider/model specific, but this does not change the public
failure contract.

### Scheduling and deployment

Concurrency is not an execution budget. The local worker remains sequential, and the deployment
target is one run executed by one agent container. Future control-plane scheduling decides how many
containers may run; no `RuntimePolicy.max_concurrency` abstraction is required now. CPU, memory,
filesystem, and placement remain container/deployment concerns.

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
- An execution budget breach becomes a structured non-retryable run failure containing the limit
  name, configured value, and safe observed value.
- A state-size breach is detected before the oversized update is checkpointed or persisted.
- A model/tool call that already started is never cancelled solely because its counter reached the
  limit; the limit prevents the next call. The run timeout remains the bound for a hung call.
- Non-idempotent external effects are never retried automatically after a budget failure or crash.

## Implementation order

1. Enforce `ModelInputBudget` for every invocation using the selected model's declared context
   window and token-counting strategy.

Thread-wide token and monetary budgets, context compression strategies, per-user quotas,
distributed rate limiting, autoscaling, and a generic quota administration system remain deferred
until a measured product need exists.
