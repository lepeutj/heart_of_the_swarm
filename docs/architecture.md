# Architecture

## Product architecture

Heart of the Swarm is an agent construction and governance platform. A natural-language Builder and
a visual editor produce the same declarative agent contract.

```text
User request ── Builder LLM ──┐
                              ▼
                         AgentSpec
                              ▲
React editor ─────────────────┘
                              ↓
                         validation
                              ↓
            provider registry + tool registry
                              ↓
                 LangChain create_agent()
                              ↓
              LangGraph-backed executable agent
                              ↓
                  run + MLflow trace + version
                              ↓
                           deploy
```

The Builder generates configuration only. The application translates trusted, validated
configuration into framework objects; it never executes generated Python.

## Control plane and runtime

The main application is the control plane. It owns agent design, validation, immutable versions,
business runs, deployment lifecycle, and the relationship to MLflow traces.

```text
Control plane                         Runtime
-------------                         -------
AgentSpec validation                  load immutable AgentVersion
AgentVersion persistence              resolve trusted model and tools
tool and model catalogues      →       create_agent()
deployment lifecycle                  invoke or stream
business status                ←       run and trace identifiers
```

The existing PostgreSQL worker queue remains the execution boundary for control-plane tests and
runs. A deployed runtime is a separate data plane: it is built from a generic runtime plus one
declarative immutable version and does not depend on the control-plane database.

## Agent responsibilities

### AgentSpec

The single portable description shared by the Builder, API, persistence, React editor, and runtime.
It contains identifiers and configuration, never model clients, tools, callbacks, credentials, or
compiled graphs.

### Validation and registries

Validation checks schema, provider support, allowed tools, permissions, and supported runtime
options. Provider and tool registries are the boundary between untrusted configuration and trusted
implementations.

### Agent runtime translation

`AgentRunner` owns one persistence-independent invocation: validate the specification, resolve the
provider model, delegate construction to `AgentFactory`, invoke the resulting graph, and return the
final assistant response. It contains no HTTP, database, queue, or business-run lifecycle logic.

`AgentFactory` is the thinner construction adapter. It resolves allow-listed tools, renders or
accepts the stored system prompt, maps the agent name, and calls `langchain.agents.create_agent`. It
must not implement a second model/tool loop.

The database worker wraps `AgentRunner` with claiming, cancellation checks, heartbeats, trajectory
persistence, and run status transitions. A future standalone runtime can reuse `AgentRunner`
without importing those worker concerns.

### Tool catalogue

The catalogue projects registered tools as safe data for the Builder and UI: identifier, name,
description, input schema, and permissions. Tool implementations remain private. LangChain, local,
MCP, and future trusted sources all feed the same registry and catalogue.

## Observability and evaluation

MLflow is the primary backend for technical traces and evaluation. LangChain/LangGraph autotracing
captures framework spans; Heart of the Swarm adds product identifiers such as agent, workflow,
version, deployment, run, node, and trace identifiers.

PostgreSQL stores immutable product versions, business status, deployment lifecycle, audit events,
and the MLflow trace reference. Existing detailed PostgreSQL trajectories remain temporarily for
parity verification and are reduced after MLflow covers the required model and tool information.

Persistence is split by product responsibility rather than exposed through one universal data
access object:

```text
AgentRepository          agents, versions, Builder sessions
WorkflowRepository       drafts and immutable workflow versions
MCPServerRepository      persisted MCP HTTP source definitions
RunRepository            queue, leases, cancellation, lifecycle events
WorkflowRunRepository    workflow queue, leases, ordered node lifecycle events
ObservabilityRepository  temporary trajectories and usage aggregates
```

Each service opens a database session and selects only the repository it needs. Repository methods
currently commit their own atomic operation; a future multi-domain use case will require an explicit
unit-of-work boundary rather than nested repository commits.

Structured application logs remain operational diagnostics, not a second tracing platform.

## Deployment architecture

```text
immutable AgentVersion
        ↓
artifact manifest + generic runtime
        ↓
Docker image
        ↓
GET /health
GET /metadata
POST /invoke
        ↓
MLflow trace
```

The runtime rebuilds the executable agent from declarative configuration. It never serializes Python
objects or compiled LangGraph graphs. Credentials are injected when the service starts. Streaming,
deployment management, and VPS targets follow only after the basic local Docker contract works.

## Workflow architecture

Workflows extend the same contracts after the agent product works end to end.

```text
WorkflowSpec
    ↓
validation
    ↓
WorkflowGraphFactory
    ↓
StateGraph
```

`WorkflowSpec` remains independent from LangGraph and React Flow. LangGraph owns orchestration,
routing, checkpoints, retries, interrupts, streaming, and parallel execution. Product code owns the
declarative language, validation, capability resolution, versioning, business lifecycle, and trace
metadata.

`WorkflowGraphFactory` accepts only `ValidatedWorkflowSpec`, creates one LangGraph node per product
node, and translates declared routes into framework edges. `WorkflowNodeRunner` contains only the
product-specific operation performed inside each node. Graph traversal, scheduling, and advanced
orchestration belong exclusively to LangGraph.

An agent node can contain an inline `AgentSpec` while it is designed or point to an immutable saved
agent version. The runtime resolves either form and delegates to the same `AgentRunner`; React Flow
must not expose the internal LangChain agent loop as workflow nodes.

An immutable workflow version is queued as a `WorkflowRun`. The worker verifies frozen capability
contracts, validates the saved declaration, rebuilds `StateGraph`, and invokes it with a timeout and
recursion limit. An asynchronous event sink records product-level node start/completion/failure
events; model and tool details remain technical tracing concerns. Expired workflow leases fail
instead of replaying potentially non-idempotent connector side effects.

## Dependency direction

```text
declarative domain contracts
        ↑
validation and translation
        ↑
LangChain / LangGraph adapters
        ↑
API / persistence / UI / deployment
```

Framework, HTTP, database, and UI objects must not leak into public specifications.

## Workflow editor boundary

The workflow editor is a Vite, React, TypeScript, and React Flow application. React Flow owns only
canvas state such as positions and selection. Before validation, the editor projects that state into
the backend-owned `WorkflowSpec` shape and sends it to FastAPI.

```text
backend config models → capability catalogue → React node palette
React Flow canvas     → WorkflowSpec         → backend validation
```

The capability catalogue exposes safe JSON schemas and current runtime availability. It does not
expose Python implementations. The production frontend is built into package static assets and
served at `/workflow-editor/`; the existing agent workspace remains available at `/` during the
migration.

MCP source definitions are persisted once and read by both API and worker processes. A refresh
replaces one server catalogue atomically in the process-local registry. Capability IDs are
namespaced with a provider-safe `server__tool` form, and published workflow versions store each
referenced capability's source, origin, and schema fingerprint.

The editor supports inserting an unconnected node onto an existing edge. Removing a simple node
can reconnect its single predecessor and successor; condition branches are never inferred. Typed
inspectors edit inline and saved agents, model/tool access, condition routes, transforms, and
declarative state mappings while preserving the same JSON contract.
