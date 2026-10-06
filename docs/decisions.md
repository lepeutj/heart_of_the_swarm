# Architecture decisions

This file is the concise decision index. Git history preserves superseded detail. Contracts contain
the normative field-level rules.

## Product model

- **ADR-001/002 — Declarative, typed specifications.** Public agent and workflow definitions are
  JSON-serializable. External node config is generic JSON; validation converts it to typed config.
- **ADR-006 — Separate versions.** Specification schema versions and immutable saved object
  versions solve different problems.
- **ADR-013 — AgentSpec is shared.** Builder, UI, API, persistence, export, and runtime use the same
  contract. Builder output is configuration, never executable code.
- **ADR-019/022/026 — One agent node.** An `AGENT` uses either an inline `AgentSpec` or an immutable
  `AgentVersion`. Inline model nodes are agents; deterministic capability calls are connectors.
- **ADR-024 — Control flow differs from data flow.** Edges order execution. Named state mappings
  move multiple inputs and outputs.

## Framework boundary

- **ADR-003/017/018 — Validate, then translate.** Validated declarations translate directly to a
  LangGraph `StateGraph`; there is no intermediate execution-plan engine.
- **ADR-014 — Delegate execution.** LangChain `create_agent` owns model/tool loops. LangGraph owns
  traversal, routing, checkpoints, interrupts, streaming, and parallel scheduling.
- **ADR-007/008 — Restricted language.** State paths, transforms, and conditions use closed,
  non-executable syntax. Schema v1 remains acyclic.
- **ADR-011 — Follow selected routes.** Product code adapts nodes; it does not implement graph
  traversal.
- **ADR-038 — Map advanced features to LangGraph.** Loops, subworkflows, reducers, commands, and
  interrupts must use framework primitives behind declarative contracts.

## Capabilities and MCP

- **ADR-005/012 — One trusted registry.** Models and tools resolve through allow-listed registries.
  A connector invokes exactly one registered capability.
- **ADR-027/028 — MCP extends that registry.** Discovery produces normal LangChain tools. Server
  failures are isolated and MCP result normalization happens once at the registry boundary.
- **ADR-029 — Persist sources, freeze use.** MCP server configuration is product data. Published
  versions record capability identity, origin, source, and schema fingerprint.

## Persistence and observability

- **ADR-015/025 — Split ownership.** PostgreSQL stores product definitions, lifecycle, audit, and
  trace references. MLflow stores technical traces, usage, and evaluation.
- **ADR-030/031 — Durable runs, portable runner.** Runs persist lifecycle and results. An already
  loaded immutable version is executed by a persistence-independent runner.
- **ADR-036 — Thread, run, checkpoint are distinct.** An `ExecutionThread` is logical continuity; a
  `WorkflowRun` is one attempt; LangGraph checkpoints are technical execution state. Resume creates
  a new run in the same thread from the latest explicitly selected checkpoint; older checkpoints
  are not time-travel resumes because they may replay external side effects.
- **ADR-037 — Execution policy differs from deployment policy.** Time and recursion limits belong to
  execution; CPU, memory, network, filesystem, and placement belong to deployment.

## Triggers and deployment

- **ADR-032/033 — Triggers are outside workflows.** Manual, webhook, and schedule triggers adapt an
  event into existing validated run creation.
- **ADR-016/023/034 — Generic runtimes plus immutable artifacts.** Export declarations and load them
  into a generic runtime. Do not generate services or serialize framework graphs.
- **ADR-035 — V1 is frozen.** Consolidation and demonstration precede a deployment control plane.

## V2 composition

- **ADR-039 — Immutable subworkflows.** Composition references a published `WorkflowVersion`, with
  typed parent/child mappings, bounded nesting, and recursive dependencies rejected. The child is a
  native LangGraph subgraph in the same run/thread, with contextual event paths and isolated state.
- **ADR-041 — Parallelism is graph structure plus declared state reduction.** Ordinary unconditional
  edges express fan-out and required fan-in; there is no public `PARALLEL` node. Concurrent writes
  require `append` or conflict-free `merge_dict`; `replace` permits only one writer. LangGraph owns
  concurrent scheduling and checkpointing.
- **ADR-042 — A supervisor makes bounded single-target handoffs.** A supervisor decision is a
  validated `handoff` to one allowed `AGENT` node or `finish`; it is not free-form workflow output.
  The target receives the decision task plus explicitly mapped inputs, returns through explicit
  output mappings, and always yields control back to the supervisor. `max_handoffs` counts transfers,
  not node visits. LangGraph commands implement routing internally; there is no public `HANDOFF`
  node.
- **ADR-009/043 — Human approval is a durable interruption.** `HUMAN_APPROVAL` requests one boolean
  product decision, while a neutral `WorkflowInterruption` persists the checkpoint, response
  contract, and resolution lifecycle. No worker blocks while waiting. An accepted response is
  validated and claimed once, then creates a new run in the same execution thread and resumes
  LangGraph with `Command(resume=...)` without replaying completed nodes.
- **ADR-040 — Security is cross-cutting.** Identity, authorization, secret references, bounded
  execution, safe errors, and secure trace/result transport apply across V2; credentials never
  enter specifications.

## Current implementation limits

- Schema v2 supports one bounded conditional back edge; nested or overlapping loops are deferred.
- Subworkflow artifact bundling, nested parallel regions, multi-target handoffs, human approval,
  remote control, and deployment lifecycle remain roadmap items. One validated fan-out/fan-in
  region uses native LangGraph scheduling, isolated branch frames, deterministic reducers, and
  checkpoint-safe resume. V2.4a implements one bounded single-target supervisor through native
  LangGraph commands.
- Do not add a second capability catalogue, custom agent loop, custom traversal engine, or duplicate
  technical tracing pipeline.
