# Domain model

## Agents

`AgentSpec` is the shared Builder, UI, API, and runtime contract. It includes Markdown Skill
identifiers alongside instructions, one model, and allowed tool identifiers:

```text
name, goal, instructions, model, allowed tool identifiers
```

It contains no clients, credentials, callbacks, Python objects, or tool implementations.
`AgentRunner` validates it, resolves the model and tools, and delegates execution to
`langchain.agents.create_agent`.

An `AgentVersion` is an immutable snapshot with its rendered system prompt. Editing a saved agent
creates a new version; runs and deployments remain pinned to their original version.

## Tools and models

Models come from the provider registry. Validation ensures that the selected model exists and
supports tool calling when tools are selected. Tools come from one allow-listed registry and are
exposed as safe catalogue data. Persisted HTTP MCP sources discover tools into that same registry;
they do not introduce another `AgentSpec` format or capability runtime.

## Workflows

`WorkflowSpec` is executable graph data. One `agent` node has either an inline or immutable-version
source. A `connector` invokes one registered capability without model choice. Conditions and
transforms use the restricted languages in `docs/workflow-spec.md`.

`ValidatedWorkflowSpec` contains typed configuration but no runtime objects. The only runtime path
is:

```text
ValidatedWorkflowSpec → WorkflowGraphFactory → LangGraph StateGraph
```

## Persistence

- `WorkflowRecord`: mutable draft JSON, layout, and revision; it may be incomplete.
- `WorkflowVersionRecord`: immutable specification, layout, and referenced capability contracts.
- `MCPServerRecord`: persisted HTTP discovery source shared by API and worker processes.
- `WorkflowEditorDocument`: node positions and viewport; never affects execution.
- `RunRecord`: lifecycle for one immutable agent or workflow version.
- `run_id ↔ mlflow_trace_id`: link between product lifecycle and technical trace.

PostgreSQL stores product state and audit information. MLflow owns detailed framework tracing and
evaluation. Executable LangChain/LangGraph objects are always rebuilt from declarations.

## Agent artifacts

An `AgentArtifactManifest` identifies one immutable `AgentVersion`, its provider and tools, the
runtime version, and the SHA-256 hash of `agent.json`. Exported artifacts contain no credentials and
require no database after export. The generic Docker runtime validates the artifact, resolves its
trusted capabilities, and rebuilds the agent with `create_agent`.
