# Architecture decisions

## ADR-001 — Declarative workflow definitions

**Status:** Accepted

Workflow specifications contain serializable configuration, never executable source code. This
supports safety, validation, reproducibility, versioning, and observability.

## ADR-002 — Generic external, typed internal node configuration

**Status:** Accepted

`WorkflowNode.config` remains `dict[str, Any]` in the public format. The validator converts it to a
node-specific Pydantic model before runtime adaptation.

## ADR-003 — Validation, compilation, and execution are separate

**Status:** Superseded by ADR-018

```text
WorkflowSpec → Validator → validated model → intermediate representation → runtime
```

This separation introduced a custom intermediate representation. Validation remains separate, but
ADR-018 removes the redundant layer and translates validated configuration directly to LangGraph.

## ADR-004 — Provider- and framework-independent core

**Status:** Accepted

Core workflow types do not depend on OpenAI, OpenRouter, LangChain, LangGraph, FastAPI, databases, or
the future visual editor. Adapters and runtime layers may use those technologies.

## ADR-005 — Explicit capability registries

**Status:** Accepted

Workflow documents reference tool and provider identifiers. Executable implementations come only
from trusted application registries.

## ADR-006 — Independent schema and saved-workflow versions

**Status:** Accepted

`schema_version` versions the workflow language. A future `workflow_version` will identify immutable
saved revisions. Multiple saved revisions can use the same schema version.

## ADR-007 — Restricted state-path and condition languages

**Status:** Accepted

State paths support only `$.` plus dot-separated identifiers. Conditions use a closed operator set.
No Python, `eval`, Jinja execution, arbitrary JSONPath, filters, or predicates are permitted.

## ADR-008 — Deterministic DAG for workflow schema v1

**Status:** Accepted

Version 1 rejects all cycles. Condition nodes provide ordered exclusive routing with one fallback.
Parallelism, merges, human approval, and bounded loops require explicit later constructs.

## ADR-009 — Human approval requires durable resume

**Status:** Accepted

Human approval is deferred until checkpoint persistence, pause/resume, node attempts, and recovery
exist. A process-local approximation is not acceptable.

## ADR-010 — Deterministic intermediate workflow data

**Status:** Superseded by ADR-018

The former compiler produced a serializable copy of typed nodes and explicit control-flow metadata.
That representation was deterministic but repeated information already available in the validated
workflow and LangGraph structure, so ADR-018 removes it.

## ADR-011 — Deterministic execution follows selected control flow

**Status:** Superseded by ADR-014

The deterministic executor starts at the compiled entrypoint and follows node targets until one
output is reached. It does not execute every node in topological order because condition branches are
exclusive. Transform values resolve from one pre-transform snapshot before writes are applied to a
copied state, avoiding assignment-order dependencies and caller-input mutation.

This executor remains a temporary behavioral reference while equivalent LangGraph execution is
verified. It must not receive new orchestration features and will be removed after migration.

## ADR-012 — Tool nodes invoke one allow-listed capability

**Status:** Superseded by ADR-022

A `tool` node stores only a registered identifier and declarative arguments. The runtime adapter
never embeds executable tools in saved configuration. Every tool identifier is preflighted
against the injected shared registry, arguments are resolved through the common state-reference
language, and the tool is invoked exactly once. Retries and model-directed tool selection are
separate concerns.

## ADR-013 — AgentSpec is the primary product contract

**Status:** Accepted

The Builder LLM and React editor produce and edit the same declarative `AgentSpec`. A validated spec
is saved as an immutable `AgentVersion`, then translated into trusted LangChain objects for testing,
tracing, and deployment. Frontend-specific agent formats and generated executable Python are not
allowed.

The agent product is completed end to end before workflow-building work resumes.

## ADR-014 — LangChain and LangGraph provide execution

**Status:** Accepted

Autonomous agents use `langchain.agents.create_agent`. Declarative workflows are translated into
LangGraph `StateGraph` instances. LangGraph owns graph traversal, routing, checkpoints, retries,
interrupts, streaming, and parallel execution.

Heart of the Swarm retains product-specific configuration, validation, capability resolution,
permissions, versioning, business lifecycle, and metadata propagation. It does not maintain a custom
agent loop or production workflow orchestration engine.

## ADR-015 — MLflow is the primary technical observability backend

**Status:** Accepted

MLflow autotracing records LangChain and LangGraph technical execution and supports evaluation.
Heart of the Swarm adds product identifiers to those traces. PostgreSQL stores product versions,
business lifecycle, audit events, and the MLflow trace reference.

Detailed PostgreSQL trajectories remain only during migration and parity verification. Duplicated
technical payloads are removed after the required trace and UI behavior is available through MLflow.
LangSmith may be added later as an optional adapter, but no second tracing pipeline is built now.

Runtime services create one product-level root span and enable MLflow's LangChain autotracing so
LangGraph nodes, model calls, and tool calls remain nested in the same technical trace. Product
`trace_id` and `run_id` remain independent identifiers stored as attributes; the MLflow trace ID is
returned separately when tracing is active. Autotracing failure must not disable the runtime or
manual root-span tracing.

## ADR-016 — Deploy immutable declarations through a generic runtime

**Status:** Accepted

A deployment references an immutable `AgentVersion` or, later, `WorkflowVersion`. Packaging combines
the declarative version and an artifact manifest with the generic Heart of the Swarm runtime. The
runtime reconstructs LangChain/LangGraph objects when it starts.

Compiled Python objects and framework graphs are never serialized. Secrets are injected externally.
The first target is local Docker with `/health`, `/metadata`, and `/invoke`; streaming and additional
targets follow after this contract is stable.

## ADR-017 — Establish the LangGraph workflow boundary before the visual editor

**Status:** Accepted

The sequencing statement in ADR-013 that deferred every workflow-building activity until the agent
product was complete is superseded. `AgentSpec` remains the primary autonomous-agent contract, but
the validated workflow-to-LangGraph boundary is established before React Flow development.

React Flow and the Builder LLM both edit the same framework-independent `WorkflowSpec`. Neither
defines execution behavior. A thin runtime adapter translates validated configuration into
LangGraph nodes and edges. This prevents the UI from becoming the data model and prevents Heart of
the Swarm from implementing a second orchestration engine.

## ADR-018 — Translate validated workflows directly to LangGraph

**Status:** Accepted

The custom intermediate workflow representation and deterministic traversal runtime are removed.
They duplicated nodes, edges, ordering, and traversal already owned by LangGraph without serving a
second active backend or deployment requirement.

The runtime path is now:

```text
WorkflowSpec → WorkflowValidator → ValidatedWorkflowSpec → WorkflowGraphFactory → StateGraph
```

Validation remains framework-independent and checks product contracts, capabilities, safe state
references, and graph semantics. `WorkflowGraphFactory` is a framework adapter and rejects raw
configuration. Product node operations may resolve trusted tools or transform state, but LangGraph
alone owns traversal, scheduling, routing, retries, checkpoints, interrupts, and parallelism.

## ADR-019 — Agent workflow nodes support inline and saved sources

**Status:** Superseded by ADR-022

An `AGENT` node may embed the existing `AgentSpec` for workflow-local design or reference one
immutable saved `AgentVersion`. It never duplicates the fields of `AgentSpec` in another model.

Both forms delegate execution to `AgentRunner` and therefore to `langchain.agents.create_agent`.
Saved versions are resolved through a persistence-independent interface. A future immutable
workflow version must freeze an inline specification or replace it with a precise agent-version
reference before deployment.

## ADR-020 — React Flow is a projection of backend workflow contracts

**Status:** Accepted

The visual editor keeps layout and selection as frontend state, but serializes executable content
to the existing `WorkflowSpec`. It does not introduce a second workflow schema and does not define
runtime behavior.

FastAPI exposes the backend workflow schema, per-node configuration schemas, runtime availability,
and structured validation issues. The editor uses that catalogue to present supported nodes and
submits the resulting declaration to backend validation. LangGraph remains the execution engine;
React Flow remains an editing surface.

## ADR-021 — LLM workflow nodes perform one tool-free model call

**Status:** Superseded by ADR-022

An `LLM` node resolves one string from workflow state, sends the configured instruction and input
to one provider-resolved LangChain chat model invocation, and writes the assistant content to the
declared output path. It does not bind tools, make autonomous decisions, or implement an agent
loop. Tool-enabled model behavior remains an `AGENT` concern and delegates to `create_agent`.

## ADR-022 — Visual LLM nodes are inline agents; saved agent nodes reference versions

**Status:** Superseded by ADR-026

The visual `LLM` node embeds the shared `AgentSpec`, including its model, instructions, and allowed
tools. Runtime translation delegates it to `AgentRunner` and `langchain.agents.create_agent`.

The visual `AGENT` node references one immutable `AgentVersion`. Saving an inline LLM configuration
as an agent creates an immutable version and replaces the node configuration with that reference.
Standalone workflow `TOOL` nodes are removed. Local, LangChain, and future MCP tools all enter
through the central agent tool registry.

Workflow drafts use optimistic revisions and store executable `WorkflowSpec` data separately from
non-executable `WorkflowEditorDocument` layout. Publishing validates the workflow and every inline
agent configuration before creating an immutable `WorkflowVersion`.

## ADR-023 — Deploy exported AgentVersion artifacts through one generic runtime

**Status:** Accepted

An exported agent artifact contains `manifest.json` and `agent.json`. It references exactly one
immutable `AgentVersion`, includes an integrity hash, and contains no secrets or runtime objects.

The standalone image is generic. At startup it loads the mounted artifact, validates its integrity,
resolves the configured provider and allow-listed tools, and rebuilds the agent through the existing
`AgentRunner` and `langchain.agents.create_agent`. It never connects to the control-plane database.

The first runtime contract exposes only `/health`, `/metadata`, and `/invoke`. Streaming, deployment
lifecycle, MCP sources, and workflow deployment remain separate later milestones.

## ADR-024 — Separate workflow execution edges from named state mappings

**Status:** Accepted

Workflow edges express execution order and routing. Agent-node `inputs` and `outputs` map named
values to and from shared LangGraph state. A node can therefore consume and produce multiple fields
without introducing data ports or a second data-flow graph in React Flow.

One textual agent result needs no response schema. Multiple named outputs require an object JSON
Schema whose mapped properties are required. Heart of the Swarm passes that schema to LangChain
`create_agent`, consumes its validated `structured_response`, and atomically projects the selected
fields into workflow state. It does not parse model-generated JSON itself.

This decision does not authorize multiple unconditional execution edges. Parallel branches, joins,
and concurrent updates require explicit LangGraph reducers and merge semantics before being exposed
by the workflow language or editor.

## ADR-025 — Split persistence by product responsibility

**Status:** Accepted

The former universal `Repository` mixed agent versions, workflow drafts, worker leases, business
events, technical trajectories, and usage aggregation. It forced unrelated services and tests to
depend on one growing persistence surface.

Persistence adapters are now separated into `AgentRepository`, `WorkflowRepository`,
`RunRepository`, and `ObservabilityRepository`. Services retain transaction ownership and select
the smallest repository matching their responsibility. No compatibility facade is kept: new code
must not restore a universal repository or hide cross-domain transactions behind delegation
wrappers.

## ADR-026 — Unify agent nodes and add deterministic connectors

**Status:** Accepted

One workflow `AGENT` node selects an inline `AgentSpec` or an immutable `AgentVersion`. A model-only
call is an `AgentSpec` without tools or Skills; LangChain `create_agent` remains the only execution
path. Skills are Markdown files resolved into the prompt and have no runtime of their own.

A `CONNECTOR` invokes exactly one registered capability and maps its result into shared state. The
same implementation may be exposed as an agent tool, but the workflow invocation is deterministic.
Schema v1 still rejects non-conditional fan-out, cycles, and concurrent state writes. Exclusive
condition routes may converge; parallel joins require explicit reducer semantics.

## ADR-027 — MCP discovery extends the shared tool registry

**Status:** Accepted

The existing `ToolRegistry` remains the only source of executable capabilities for agents and
deterministic connectors. Registered entries carry source and optional origin metadata, and the
registry accepts trusted capabilities after construction without replacing existing IDs.

At application startup, LangChain's MCP adapter discovers remote HTTP MCP tools and registers the
returned `BaseTool` objects with `source="mcp"`. `AgentFactory` and `CONNECTOR` remain independent
of that source and continue resolving IDs through the same registry. Name collisions fail startup;
they are never silently overwritten. Credentials, UI-managed server configuration, and live
catalogue refresh remain later concerns.

## ADR-028 — MCP failures are isolated and results normalize at the registry boundary

**Status:** Accepted

MCP tools are invoked with LangChain tool-call envelopes so error status and structured artifacts
are preserved. The shared `ToolRegistry` normalizes those framework envelopes before a connector
maps the result into workflow state. React and workflow nodes do not implement MCP-specific result
handling.

Each MCP server is discovered independently. A failed discovery or refresh records that server's
status without blocking built-in capabilities, healthy MCP servers, the API, or the worker. Refresh
replaces one source catalogue atomically and preserves its previous tools when discovery fails.
This supersedes ADR-027's requirement that one MCP collision or outage fail application startup.

## ADR-029 — Persist MCP sources and freeze capability contracts

**Status:** Accepted

MCP HTTP source definitions are product configuration stored in PostgreSQL and read by both API and
worker processes. Runtime adapters and discovered tools remain process-local. The existing
`ToolRegistry` remains the only executable catalogue; persistence does not introduce a second
capability abstraction.

Refresh is serialized per source and replaces that source atomically. Previous adapters remain
alive until process shutdown so in-flight calls are not invalidated. MCP capability IDs use a
provider-safe `server__tool` namespace because common model APIs reject dots and other punctuation
in function names.

Publishing a `WorkflowVersion` stores the identifier, source, origin, and schema fingerprint for
every connector and agent tool it references. Execution must reject a missing or incompatible
runtime capability rather than silently using a changed remote contract.

## ADR-030 — Workflow runs are durable but are not replayed without checkpoints

**Status:** Accepted

An immutable `WorkflowVersion` is queued in a dedicated workflow-run table and executed by the
existing worker through `WorkflowGraphFactory` and LangGraph. PostgreSQL stores ordered workflow and
node lifecycle events plus the final structured result. These events are product audit data, not a
replacement for MLflow model/tool tracing.

Execution is bounded by a wall-clock timeout and LangGraph recursion limit. A worker lease expiry
marks the run failed. Automatic replay is forbidden until durable LangGraph checkpoints and
idempotency semantics can prove that external connector side effects will not be duplicated.

## ADR-031 — Execute loaded workflow versions behind a portable runner

**Status:** Accepted

`WorkflowVersionRunner` receives an already-loaded immutable `WorkflowVersionDetail`, invocation
input, runtime policy, and optional event sink. It validates the frozen capability contracts and
input, builds the LangGraph graph, applies execution bounds, emits neutral runtime events, and
returns `ExecutionResult`.

The runner never loads versions, creates or updates runs, manages worker leases, or writes to a
database. API services validate input through the same shared function before queueing. Workers own
claiming, heartbeat, and lifecycle persistence; future artifact and remote runtimes may call the
same runner after loading a version through their own adapter.

## ADR-032 — Triggers are typed definitions outside workflows

**Status:** Accepted

A trigger decides when one immutable agent or workflow version should create a run. It is not a
workflow node and never implements waiting, polling, or repeated execution inside LangGraph.

V1 defines strict manual, webhook, and interval-schedule configurations. Persistent trigger records
store `target_type` and `target_version_id`; the service verifies that the referenced immutable
version exists before insertion. A relational foreign key is intentionally not used because one
column can target either the agent-version or workflow-version table. Version deletion is not
supported, and target validation remains a service invariant.

Trigger persistence does not create runs by itself. Webhook ingestion and scheduler claiming are
separate adapters that will validate input and call the existing run-creation service.

## ADR-033 — Webhooks adapt external events into existing run creation

**Status:** Accepted

`WebhookTriggerService` resolves a trigger and verifies that it is enabled, is a webhook, and
targets a workflow. It does not load the workflow version, validate workflow input, create database
run records, or know the worker and LangGraph runtime. It passes the JSON object and trigger-origin
metadata to `WorkflowRunService`, which retains those responsibilities.

Every invocation generates a distinct `trigger_event_id`. Workflow runs persist the trigger ID,
type, and event ID for audit and future idempotency work. V1 performs no payload transformation and
does not claim full idempotence. The external route is `/api/v1/hooks/{trigger_id}`; trigger
management remains under `/api/v1/triggers`.

## ADR-034 — Export workflow declarations through the portable runner

**Status:** Accepted

A standalone workflow artifact contains `manifest.json` and `workflow.json`. It freezes one
immutable workflow version, its capability contracts, and an integrity hash; it contains no
editor layout, database settings, secrets, runtime objects, or serialized LangGraph graph.

The generic workflow image loads the artifact, resolves built-in capabilities, validates the
declaration, and executes it through the same `WorkflowVersionRunner` used by the worker. The
runtime exposes `/health`, `/metadata`, and `/invoke` and never connects to the control-plane
database. V1 rejects MCP capabilities, referenced agent versions, and external Skill files because
those dependencies are not yet bundled. A deployment must fail explicitly rather than start with
an incomplete execution environment.

## ADR-035 — Freeze V1 before deployment lifecycle management

**Status:** Accepted

The demonstrated V1 boundary ends at a generic Docker runtime invoking an integrity-checked
immutable workflow artifact and emitting a correlated MLflow trace. The product does not yet own
container builds, image registries, remote hosts, runtime instances, health management, restart,
rollback, or secret distribution.

Those capabilities form a deployment control plane and are a V2 subsystem, not incremental V1
runtime work. V1 consolidation prioritizes a reproducible demonstration, accurate architecture
documentation, removal of genuinely superseded code, and explicit known limits. Existing legacy
workflow readers remain only where they migrate tested saved-document formats.

## ADR-036 — Separate execution threads, runs, and checkpoints

**Status:** Accepted

V2 introduces `ExecutionThread` as the durable identity of resumable workflow state. A
`WorkflowRun` remains one invocation attempt and references exactly one thread. Initial invocation,
resume, and operator retry create separate runs instead of mutating one run across multiple
execution periods.

LangGraph's checkpointer owns technical state snapshots under the product thread identifier.
PostgreSQL product records own thread/run lifecycle and audit events; MLflow owns technical traces.
Checkpoint payloads are not copied into workflow-run records. The first V2 thread contract pins one
immutable `WorkflowVersion`.

`RunCreationService` generates and persists the product thread ID during the initial atomic
thread/run creation. A resume always creates a new run in the same thread and records the previous
run in `resumed_from_run_id`, the selected checkpoint in `resume_checkpoint_id`, and its position in
`attempt_index`. The existing run is never reopened. Each run receives its own product trace and
MLflow trace; shared thread metadata links them into one history.

Explicit interruption marks the run and thread interrupted. A worker crash marks the run failed
while preserving the last committed checkpoint. Neither case starts a new run automatically.
Resume requires an explicit request or a future recovery policy that has proven side-effect safety.
A resume continues from the selected LangGraph checkpoint; it is not a new invocation from the
workflow entrypoint and must not replay a connector completed before that checkpoint.

The portable runner receives thread identity and a checkpointer adapter from its caller. It does
not create product threads or import a PostgreSQL repository.

## ADR-037 — Separate execution and deployment policies

**Status:** Accepted

`ExecutionPolicy` bounds runtime behavior: time, graph steps, iterations, model calls, and tool
calls. `DeploymentPolicy` later bounds infrastructure: CPU, memory, concurrency, filesystem,
network, and allowed hosts. One universal runtime policy would mix incompatible ownership and make
portable execution difficult to validate.

Application and deployment ceilings may tighten a version's execution policy but never loosen it.
Nested agents and subworkflows share the parent's counters.

## ADR-038 — Map advanced workflow declarations to LangGraph primitives

**Status:** Accepted

V2 does not introduce public `LOOP`, `PARALLEL`, or `HANDOFF` node types. Bounded back edges express
controlled cycles, multiple validated edges express parallel branches, declarative reducers resolve
concurrent state writes, and allow-listed agent targets translate internally to LangGraph
`Command`.

`Command`, reducer callables, checkpoint objects, and compiled subgraphs remain runtime details.
Public specifications contain only closed identifiers and validated configuration. `AGENT` remains
opaque until a later use case proves that embedding its internal graph is necessary.

## ADR-039 — Compose workflows through immutable subworkflow versions

**Status:** Accepted

A V2 `SUBWORKFLOW` node references one immutable `WorkflowVersion` and declares named input/output
mappings. Publication validates the child schemas and rejects recursive dependency graphs. Runtime
resolution is injected into the portable runner; the runner never loads persistence itself.

Standalone artifacts must either bundle the complete transitive workflow dependency set or reject
export explicitly. They never serialize a compiled LangGraph graph.

## ADR-040 — Treat security as a cross-cutting V2 constraint

**Status:** Accepted

Remote identity and deployment control are later V2 milestones, but new runtime features must
preserve external secret injection, capability allow-lists, safe errors, bounded inputs/state,
explicit side-effect idempotency, and restricted network access. Public-network deployment is
forbidden until API/runtime authentication and transport security exist.
