# Heart of the Swarm

Heart of the Swarm is an agent engineering workbench. It creates useful AI agents from declarative
configuration, freezes them as immutable versions, runs them with observable model/tool behavior,
and can export them to an autonomous runtime. Explicit LangGraph workflows remain available for
composing agents and deterministic steps, but they are not the primary creation experience.

```text
Builder LLM or React editor
        ↓
AgentSpec / WorkflowSpec
        ↓ validation and trusted registries
LangChain create_agent / LangGraph StateGraph
        ↓
run, trace, version, deploy
```

The visible product story is deliberately small:

```text
BUILD       AgentSpec → immutable AgentVersion
RUN         task → model/tools → result
UNDERSTAND  events → trajectory → trace → metrics
DEPLOY      AgentVersion → artifact → autonomous runtime
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

## Current product priority

The current priority is a dedicated Agent Studio that exposes the agent capabilities already
implemented by the API and runtime. A user must be able to generate or edit an `AgentSpec`, choose
its model, tools, and Skills, validate it, create an immutable version, run a task, and inspect the
result and trajectory without first creating a workflow node.

The product surfaces have separate purposes:

```text
Agent Studio     build, version, test, and understand one intelligent unit
Workflow Studio  compose agents and deterministic operations explicitly
Remote Runtime   execute one immutable version in another environment
```

The deployment registry and automated provisioning are deferred. The existing artifact and remote
runtime are sufficient for the next portfolio demonstration. A Deep Agents integration is also
deferred until an isolated comparison proves that `create_deep_agent` adds enough value over the
existing `create_agent` runtime on the same specification, tools, model, and task.

## Current state

Implemented: agent builder, provider and tool registries, immutable agent versions, asynchronous
runs, trajectories, usage metrics, Docker stack, workflow validation/runtime, React Flow editing,
and persisted workflow drafts with separate layout metadata.

The current React application is primarily a Workflow Studio. Inline agents can be configured and
saved from an `AGENT` node, but this hides the already-implemented direct agent APIs behind a
workflow-oriented path. The missing Agent Studio is therefore a product and UX gap, not a missing
agent runtime. Selecting `INPUT` in the existing workflow editor edits the workflow invocation
contract, while selecting `OUTPUT` edits named final results and their optional types.

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
matched that route. V2.5a now compiles a typed `HUMAN_APPROVAL` node to LangGraph `interrupt()`,
creates a durable checkpoint, persists one pending interruption, and releases the current worker
attempt as interrupted without writing an approval value into workflow state. The application
service now validates one boolean response, atomically resolves the interruption, creates a new run
in the same execution thread, and resumes LangGraph without replaying completed nodes. HTTP adapters
list and read interruption state and accept one boolean response. React editing and run interaction
now expose a typed approval node, show pending requests, submit approve/reject decisions, and follow
the returned continuation run while preserving prior attempt events.

V2.5b-1 replaces React run polling with reconnectable SSE over durable product events. PostgreSQL
remains the product event source and does not store model-token streams. V2.6a now has a documented
default-deny authorization contract separating user, agent-version, workflow-node, and runtime
principals from protected capabilities and credential references.

The pure V2.6a authorization value objects and exact-match `PolicyEvaluator` are now implemented.
They reject unknown contract values, deny missing policy, apply restrictive decision precedence,
and remain independent from FastAPI, JWT, persistence, LangGraph, credentials, and remote runtimes.

V2.6c now maps every authenticated control-plane operation to an exact action and trusted resource
identity before execution. Development access is expanded into explicit process-local policies for
persisted and newly created resources; it uses the same default-deny evaluator as future persistent
policy sources. Denied requests return a safe `403`, emit an audit decision, and perform no protected
side effect.

V2.6d now propagates immutable `agent_version` and `workflow_node` principals into workers and
standalone runtimes. Agents receive isolated model-facing tool views filtered before
`create_agent`, every tool and connector invocation is checked again, and the shared registry is
never mutated per run. Exact runtime allows currently come from `CAPABILITY_POLICIES`; missing
policy is denied, while managed policy persistence remains deferred.

V2.6e now separates capability permission from runtime-owned secret resolution. MCP HTTP sources
may persist a public bearer `CredentialRef`; workers and standalone runtimes resolve it only after
an exact `credential.resolve` allow and inject the value directly into FastMCP's transport. The
control-plane API receives no local secret store, and authenticated catalogue synchronization back
to that API remains a documented follow-up.

V2.6f now has canonical capability-argument fingerprints and durable request-bound grants. An
approved capability interruption creates its continuation run and exact expiring grant in one
transaction. Sequential deterministic `CONNECTOR` nodes now suspend through LangGraph when policy
requires approval, recheck policy on resume, consume the exact grant atomically, and only then
invoke the capability. Sequential root-workflow agents use a LangChain middleware and inherited
LangGraph subgraph checkpoints to preserve the model-selected tool call across the same approval
flow without replaying the model decision. Multiple approval-gated calls in one model turn,
parallel-region approvals, nested-subworkflow approvals, and standalone approval brokers remain
deferred.

V2.7 now exposes the standalone agent and workflow runtimes through an exact Bearer service token.
Invocation is asynchronous: the runtime assigns run and trace identifiers immediately, executes the
existing persistence-independent runner in the background, and exposes safe in-memory status,
result, MLflow trace correlation, and ordered lifecycle events. This runtime-local state is
intentionally ephemeral; remote deployment records and lifecycle belong to the future control
plane. The thin `RemoteRuntimeClient` consumes the same typed metadata, invocation, run, and event
contracts without knowing LangChain, LangGraph, Docker, or persistence.

V2.7c stabilizes that boundary around one active run per long-lived runtime, correlated safe error
logs, export-time rejection of unsupported human approval, and detailed workflow events carrying
run and trace identity. The obsolete unauthenticated dashboard redirects to the authenticated React
application. A deployment is durable availability of one immutable version; a run remains one
bounded task. Registration and automated provisioning are now deferred until the Agent Studio,
portfolio examples, and one reproducible remote demonstration are complete.

The first portfolio data example is now defined as a separate read-only sales database and FastMCP
server. PostgreSQL enforces a dedicated SELECT-only role, MCP exposes bounded parameterized business
queries, and the same discovered capabilities can be used by a deterministic `CONNECTOR` or selected
autonomously by an agent. This example is development infrastructure, not a second capability
registry or a production credential pattern.

Direct Agent Studio runs currently have no conversational continuity. After the exported-version
Docker/Raspberry demonstration, durable agent conversations will pin one immutable `AgentVersion`,
persist ordered user/assistant messages, and create one bounded run per user turn. Workflow
`HUMAN_APPROVAL` remains a separate boolean decision primitive and must not be presented as chat.

Persistence and observability internals are organized by responsibility. Avoid universal service
or repository classes; composition belongs in `Application`, while product services choose narrow
repositories and framework adapters.
