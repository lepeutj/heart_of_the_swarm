# Authenticated Remote Runtime

## Scope

V2.7 turns the database-independent standalone runtime into a remotely pilotable data plane without
changing `AgentRunner`, `WorkflowVersionRunner`, or artifact formats.

```text
immutable artifact
        ↓
generic runtime container
        ↓
authenticated asynchronous invocation
        ↓
local status, events, result, and trace identifiers
```

## HTTP contract

```text
GET  /health                  public
GET  /metadata                Bearer service token
POST /invoke                  Bearer service token
GET  /runs/{run_id}           Bearer service token
GET  /runs/{run_id}/events    Bearer service token
```

`POST /invoke` returns `202 Accepted` after creating an in-memory run:

```json
{
  "run_id": "...",
  "trace_id": "...",
  "status": "queued"
}
```

The runtime executes in the background and exposes this closed lifecycle:

```text
queued → running → completed | failed | timed_out
```

Run details contain timestamps, a safe error or final result, and the MLflow trace identifier when
available. One runtime accepts one active run at a time; another invocation receives
`409 runtime_busy` rather than entering a hidden scheduler or queue.

Every event contains:

```text
event_id
run_id
trace_id
sequence
event_type
created_at
data
```

The runtime owns `runtime.*`, `workflow.*`, and direct `agent.*` lifecycle events. Workflow node,
handoff, supervisor, and checkpoint events pass through the existing `WorkflowEventSink`. Sequence
records observation order; it does not invent a logical order between concurrent branches. Raw
provider exceptions, credentials, prompts, tool arguments, and workflow state are not event
payloads.

## Authentication boundary

`RUNTIME_API_TOKEN` is injected when the container starts. Protected endpoints accept only
`Authorization: Bearer <token>` and compare the exact token in constant time. The token is not part
of an artifact, runtime metadata, result, event, or log field.

HTTP service authentication and execution identity remain independent:

```text
RUNTIME_API_TOKEN
→ who may call the container API

RuntimeIdentity
→ which runtime may resolve credentials and act on protected resources
```

## Storage and failure semantics

V2.7 stores a bounded number of runs and events only in process memory. The oldest terminal runs
are removed when `RUNTIME_MAX_RETAINED_RUNS` is reached. Restarting the container loses all
runtime-local state. This is an explicit MVP constraint and avoids adding SQLite, PostgreSQL,
Redis, or a second product lifecycle store to the data plane.

Execution errors are reduced to safe terminal messages. Runtime logs retain the correlated run,
trace, and exception type without persisting the provider message; MLflow remains the technical
trace backend. Shutting down the process cancels active background tasks; it does not claim a
durable resume or exactly-once external side effect.

Standalone workflow export rejects dependencies that the runtime cannot complete independently,
including `HUMAN_APPROVAL` until a remote approval broker exists.

## Deferred

- deployment records and runtime administration UI;
- artifact transfer and remote Docker lifecycle;
- outbound runtime callbacks and heartbeats;
- durable runtime-local runs;
- native token streaming;
- JWT or certificate-based service authentication;
- restart recovery, retries, and rollback.

## Deployment boundary

One long-lived deployment binds one immutable version to one runtime container and accepts
successive runs:

```text
Deployment = durable availability of one immutable version
Run        = one bounded task executed by that deployment
```

Deployment registration is deferred until the Agent Studio, portfolio examples, and reproducible
remote Docker demonstration are complete. When it resumes, the first increment registers an
already-running endpoint. Automated container provisioning, artifact installation, credential
injection, health convergence, and rollback remain later work.

## Control-plane client

`RemoteRuntimeClient` is the only control-plane adapter for this HTTP contract. It exposes
`metadata()`, `invoke()`, `get_run()`, and `get_events()` and validates every response against the
shared runtime models. Authentication, network, HTTP, and invalid-response failures become stable,
credential-free errors. Polling strategy remains a caller concern; the client does not create a
second run lifecycle or know about LangChain, LangGraph, Docker, or persistence.
