# Heart of the Swarm

Heart of the Swarm visually configures AI agents and workflows, then turns the same declarative
data into LangChain/LangGraph runtimes that can be tested, traced, versioned, and deployed.

```text
Builder LLM or React editor
        ↓
AgentSpec / WorkflowSpec
        ↓ validation and trusted registries
LangChain create_agent / LangGraph StateGraph
        ↓
run, trace, version, deploy
```

## Product contracts

- `AgentSpec` contains instructions, one provider model, and allowed tool identifiers.
- An `AgentVersion` is an immutable agent snapshot used by runs and deployments.
- `WorkflowSpec` contains executable nodes and edges without React or LangGraph objects.
- A workflow draft is mutable and stores a separate `WorkflowEditorDocument` for layout.
- A `WorkflowVersion` is an immutable validated workflow snapshot.

One `AGENT` node either embeds an inline `AgentSpec` or references a saved `AgentVersion`; both
delegate their model/tool loop to `langchain.agents.create_agent`. Markdown Skills contribute
reusable instructions. Deterministic `CONNECTOR` nodes invoke one registered capability directly.

## Ownership

- Heart of the Swarm: contracts, validation, registries, versions, lifecycle, and metadata.
- LangChain: models, tools, and autonomous agent construction.
- LangGraph: workflow routing and runtime control flow.
- PostgreSQL: drafts, immutable versions, business state, and trace references.
- MLflow: technical traces and evaluation.
- React Flow: editing and layout only.

## Current state

Implemented: agent builder, provider and tool registries, immutable agent versions, asynchronous
runs, trajectories, usage metrics, Docker stack, workflow validation/runtime, React Flow editing,
and persisted workflow drafts with separate layout metadata.

The primary page is now an observation dashboard for saved definitions, recent agent runs,
failures, trajectories, and model usage. Workflow and inline-agent editing is concentrated in the
React Flow builder. Selecting `INPUT` edits the workflow invocation contract, selecting `OUTPUT`
edits named final results and their optional types, and agent data mappings remain available as an
advanced contextual section rather than permanent global forms.

Named state mappings, unified agent nodes, Markdown Skills, deterministic connectors, and optional
workflow output schemas are implemented. Remote HTTP MCP tools are discovered through LangChain,
normalized through the shared capability registry, and invoked by agents or deterministic
connectors. Discovery failures are isolated per server, and capability input schemas drive
connector arguments in the editor. MCP HTTP sources can now be added, tested, refreshed, and
inspected from the editor; their definitions are shared through PostgreSQL by the API and worker.
Published workflow versions freeze the capability identities and schema fingerprints they use.
They can be queued for durable worker execution, followed through ordered node lifecycle events,
and inspected in the editor. Invocation input is validated before queueing. An already-loaded
version can also be executed through the persistence-independent `WorkflowVersionRunner`, which is
the shared boundary for workers, artifacts, and remote runtimes.

Typed manual, webhook, and interval-schedule trigger definitions can be persisted against an exact
immutable agent or workflow version. Enabled workflow webhook triggers accept an external JSON
object, pass it unchanged through the existing synchronous validation and run-creation service, and
record trigger origin on the durable run. Scheduler claiming, payload mapping, agent webhook
targets, and trigger UI remain deferred.

Immutable workflow versions can be exported as integrity-checked declarations and executed by a
generic standalone API without PostgreSQL, the control plane, or the worker. The first portable
runtime supports built-in capabilities and inline agents. MCP capabilities, referenced agent
versions, and external Skill files are rejected until their dependencies can be bundled explicitly.
When MLflow is enabled, standalone runtimes create a product root span, enable LangChain/LangGraph
autotracing, expose the MLflow trace ID in invocation responses, and keep running if tracing is
unavailable.

The workflow runtime image has been built and invoked with a real read-only artifact. Its
database-independent result and nested MLflow trace were verified through Docker. V1 is frozen.
V2 first introduces execution policies, threads, durable LangGraph checkpoints, controlled cycles,
subworkflows, and reducers. V2.1 durable execution threads and explicit resume are complete. V2.2
supports one checkpoint-safe bounded conditional back edge and immutable nested workflow versions
with isolated mappings, hierarchical events, recursion protection, and nested checkpoint resume.
Standalone export of a parent with child workflow dependencies remains deferred. Parallel state,
including one deterministic fan-out/fan-in region, is implemented with native LangGraph scheduling,
closed reducers, contextual events, and checkpoint-safe resume. V2.4a multi-agent routing supports
one bounded supervisor-selected agent at a time, mandatory return, strict structured decisions,
contextual handoff events, and checkpoint-safe resume. The React inspector edits the same
supervisor source, inputs, allowed-target relations, task fields, and finish mapping without
creating synthetic control-flow edges. Interaction, secure remote execution, and deployment
lifecycle management remain later milestones.

The V2.4 supervisor path has also been exercised end to end with a real OpenRouter model: the
supervisor handed work to a researcher, regained control, handed the result to a reviewer, regained
control again, and finished through the declared output node. Persisted node and handoff events
matched that route. V2.5a now has a documented durable human-approval contract; its implementation
has started with a typed, validation-only `HUMAN_APPROVAL` node and durable interruption records.
LangGraph interruption, resolution API, and React editing are not implemented yet.

Persistence and observability internals are organized by responsibility. Avoid universal service
or repository classes; composition belongs in `Application`, while product services choose narrow
repositories and framework adapters.
