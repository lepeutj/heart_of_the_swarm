# V1 architecture and demonstration

Heart of the Swarm V1 turns declarative agent and workflow configuration into versioned runs and
portable LangChain/LangGraph runtimes. V1 is feature-frozen: deployment lifecycle management and
advanced orchestration belong to V2.

## Architecture

```text
React editor / Builder LLM
            ↓
AgentSpec / WorkflowSpec + layout document
            ↓ validate and publish
Immutable AgentVersion / WorkflowVersion
            ├─────────────────────────────────────────────┐
            ↓                                             ↓ export
Trigger or manual request                           integrity-checked artifact
            ↓                                             ↓
Durable run → worker                                generic Docker runtime
            ↓                                             ↓
            └──────────────→ WorkflowVersionRunner ←──────┘
                                  ↓
                         LangGraph StateGraph
                                  ↓
                 connectors / LangChain create_agent
                                  ↓
                 result + business events + MLflow trace
```

The control plane owns configuration, immutable versions, triggers, durable runs, and audit state.
The standalone runtime owns one invocation of one mounted immutable artifact. Both workflow paths
use the same `WorkflowVersionRunner`.

## Boundaries to preserve

| Product concept | Boundary |
| --- | --- |
| `AgentSpec` | Serializable configuration, never a LangChain object |
| `WorkflowSpec` | Executable declaration, never a `StateGraph` or React Flow document |
| Trigger | Decides when to request a run; it is not a workflow node |
| Run | Persistent lifecycle for one invocation; it is not the executor |
| `WorkflowVersionRunner` | Executes an already-loaded version; it does not load or persist it |
| PostgreSQL | Versions, lifecycle, business events, and audit references |
| MLflow | LangChain/LangGraph technical traces and future evaluation |
| Docker runtime | Loads and invokes an artifact; it is not a deployment control plane |

Agents always delegate their model/tool loop to `langchain.agents.create_agent`. Workflows always
delegate traversal and routing to LangGraph. The product keeps validation, capability resolution,
versioning, and lifecycle around those frameworks.

## Reproducible demonstration

### 1. Start the control plane

Requirements: Docker Desktop with Linux containers and one OpenAI or OpenRouter key for the agent
portion of the demonstration.

```powershell
Copy-Item .env.example .env
# Add OPENAI_API_KEY or OPENROUTER_API_KEY to .env.
docker compose up --build -d
docker compose ps
```

Open:

- agent workspace: <http://localhost:8000/>;
- workflow editor: <http://localhost:8000/workflow-editor/>;
- MLflow: <http://localhost:5000/>.

### 2. Ask one agent to create an RSS agent

Run the public factory demonstration against the running control plane:

```powershell
uv run --locked python examples/workflow_factory_demo.py
```

The first agent receives the public `WorkflowSpec` schema and the workflow-authoring Skill. It must
produce configuration rather than Python code. Its request is to create this second workflow:

```text
INPUT(feed_url, request) → AGENT(tools=[rss_reader]) → OUTPUT(result)
```

The generated inline agent must call `rss_reader` once with at most five entries, identify the main
themes, and answer the user's request in French. The demonstration then:

1. validates the generated `WorkflowSpec`;
2. saves a mutable workflow draft;
3. publishes an immutable `WorkflowVersion`;
4. queues a durable run;
5. lets LangChain select and invoke `rss_reader`;
6. prints the final French thematic summary and all persisted identifiers.

Pass `--model-id <openrouter-model-id>` to select another OpenRouter model that supports both
structured output and tool calling. Pass `--feed-url <rss-or-atom-url>` to use another public feed.

### 3. Inspect the generated execution

Open the workflow editor and select the generated workflow. Its graph, inline `AgentSpec`, mappings,
immutable version, run status, node events, and final result should all be visible.

Open MLflow and inspect the nested technical path:

```text
workflow.run
└── LangGraph
    └── RSS agent
        ├── model call
        ├── rss_reader
        └── model call
```

This demonstrates the product distinction directly: the designer agent creates a declaration,
LangGraph orchestrates the declared workflow, and LangChain owns the generated agent's model/tool
loop.

### 4. Export the immutable workflow version

Copy the `generated_version_id` printed by the demonstration:

```powershell
$versionId = "<generated-version-uuid>"
```

Export from the API container, which already has access to PostgreSQL, then copy the portable files
to the host:

```powershell
docker compose exec api swarm-export-workflow $versionId /tmp/workflow-artifact
$apiContainer = docker compose ps -q api
docker cp "${apiContainer}:/tmp/workflow-artifact" .\workflow-artifact
Get-ChildItem .\workflow-artifact
```

The directory must contain only `manifest.json` and `workflow.json`. This generated workflow is
portable because it contains an inline agent and the built-in `rss_reader`. Standalone workflow V1
still rejects MCP capabilities, saved-agent references, and external Skill files.

### 5. Invoke the standalone Docker runtime

```powershell
docker build -f docker/workflow-runtime.Dockerfile -t heart-of-the-swarm-workflow-runtime:v1 .
$artifact = (Resolve-Path .\workflow-artifact).Path
docker run --rm --name swarm-v1-demo -p 8080:8000 `
  --mount "type=bind,source=$artifact,target=/app/artifact,readonly" `
  -e OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY `
  -e MLFLOW_ENABLED=true `
  -e MLFLOW_TRACKING_URI=http://host.docker.internal:5000 `
  -e MLFLOW_EXPERIMENT=heart-of-the-swarm-demo `
  heart-of-the-swarm-workflow-runtime:v1
```

In another terminal:

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/metadata
$body = @{
  input = @{
    feed_url = "https://feeds.bbci.co.uk/news/technology/rss.xml"
    request = "Résume en français les principaux thèmes des cinq dernières publications."
  }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:8080/invoke -Method Post -ContentType application/json -Body $body
```

The invocation returns `run_id`, product `trace_id`, optional `mlflow_trace_id`, and the French
summary. Open MLflow and inspect the nested runtime, LangGraph, model, and `rss_reader` spans.

## Five-minute portfolio path

1. Run the designer agent and show the generated RSS workflow in React Flow.
2. Explain that the generated `AgentSpec` grants only `rss_reader` to the inline agent.
3. Show the durable run, ordered business events, French summary, and nested MLflow trace.
4. Export the same immutable generated version and start its generic Docker runtime.
5. Invoke it without the control-plane API or worker and compare the result and trace.

The demonstration proves that one governed agent can create another useful tool-using agent as a
portable declaration, and that the same immutable workflow can run durably in the product or
independently through the standalone runtime.

## Critical review

- `api.py`, `frontend/src/App.tsx`, `NodeInspector.tsx`, and `WorkflowNodeRunner` are the largest
  maintenance hotspots. Split them by product domain only when the next change requires it; a
  cosmetic V1 refactor would add risk without changing behavior.
- PostgreSQL trajectories still duplicate part of MLflow technical tracing. Keep them while the UI
  depends on them, then remove duplicated payloads only after MLflow-backed parity exists.
- Legacy `llm`, `input_path`, and `output_path` readers increase the contract surface, but they are
  tested migrations for saved drafts rather than an alternative runtime. Remove them only with an
  explicit document migration.
- MCP output schemas, credentials, OAuth, and `stdio` transports are incomplete. Remote schema
  drift is detected only through the capability contract currently exposed by the adapter.
- Schedule definitions are persisted but not claimed. V1 demonstrates manual and webhook starts,
  not continuous scheduling.
- No authentication or rate limiting protects the control plane. The stack is for local/private
  use, not direct public exposure.
- CI verifies behavior, migrations, both frontend artifacts, package construction, and both generic
  runtime image builds. The real Docker invocation remains a release smoke test because it requires
  service orchestration and an exported artifact.

## V2 backlog

- deployment records, image registry integration, remote hosts, health management, and rollback;
- scheduler claiming and trigger payload mapping;
- LangGraph checkpoints, interrupts, human approval, retries, streaming, and parallel reducers;
- MCP credentials, OAuth, `stdio`, and richer schema handling;
- authentication, users, permissions, quotas, and retention;
- guided schema and mapping editors plus richer run visualization.

## Development lesson

Complete one real path before adding an abstraction. Start from LangChain/LangGraph capabilities,
keep product contracts declarative, and test real integration boundaries such as MCP and Docker.
An improvement that is not required by the current end-to-end path belongs in the backlog.
