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

### 2. Build and run a deterministic workflow

In the workflow editor, create this graph:

```text
INPUT → CONNECTOR calculator → OUTPUT
```

Use the following contract:

- input schema: object with required string `expression`;
- connector input `expression`: `from_state = $.expression`;
- connector output `value`: `to_state = $.calculation`;
- output field `result`: `from_state = $.calculation`;
- output schema: object with required string `result`.

Save the draft, create an immutable version, enter `{"expression":"2 + 3 * 4"}`, and select
**Run**. The editor should show ordered node events and return `{"result":"14"}`.

### 3. Add the agentic path

Insert an inline `AGENT` between the connector and output. Select a provider/model, give it an
instruction to explain the calculation, map `$.calculation` into its input, and map its answer into
`$.summary`. Publish a new version and run it. This demonstrates that deterministic capability
invocation and the LangChain agent loop are separate operations in one LangGraph workflow.

An MCP capability can replace the built-in connector for a live integration demonstration. Add the
HTTP server in the MCP panel, test and refresh it, then select the discovered namespaced capability
in the connector inspector.

### 4. Export the immutable workflow version

Resolve the version UUID through the API after selecting the workflow by name:

```powershell
$workflows = Invoke-RestMethod http://localhost:8000/api/v1/workflows
$workflow = $workflows | Where-Object name -eq "V1 calculator demo" | Select-Object -First 1
$version = Invoke-RestMethod "http://localhost:8000/api/v1/workflows/$($workflow.id)/versions/latest"
$versionId = $version.id
```

Export from the API container, which already has access to PostgreSQL, then copy the portable files
to the host:

```powershell
docker compose exec api swarm-export-workflow $versionId /tmp/workflow-artifact
$apiContainer = docker compose ps -q api
docker cp "${apiContainer}:/tmp/workflow-artifact" .\workflow-artifact
Get-ChildItem .\workflow-artifact
```

The directory must contain only `manifest.json` and `workflow.json`. Export the deterministic
calculator version for this step: standalone workflow V1 deliberately rejects MCP capabilities,
saved-agent references, and external Skill files.

### 5. Invoke the standalone Docker runtime

```powershell
docker build -f docker/workflow-runtime.Dockerfile -t heart-of-the-swarm-workflow-runtime:v1 .
$artifact = (Resolve-Path .\workflow-artifact).Path
docker run --rm --name swarm-v1-demo -p 8080:8000 `
  --mount "type=bind,source=$artifact,target=/app/artifact,readonly" `
  -e MLFLOW_ENABLED=true `
  -e MLFLOW_TRACKING_URI=http://host.docker.internal:5000 `
  -e MLFLOW_EXPERIMENT=heart-of-the-swarm-demo `
  heart-of-the-swarm-workflow-runtime:v1
```

In another terminal:

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/metadata
$body = @{ input = @{ expression = "40 + 2" } } | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:8080/invoke -Method Post -ContentType application/json -Body $body
```

The invocation returns `run_id`, product `trace_id`, optional `mlflow_trace_id`, and output `42`.
Open MLflow and inspect the nested runtime, LangGraph node, and calculator spans.

## Five-minute portfolio path

1. Show the graph and explain that edges carry control while shared state mappings carry data.
2. Publish and run `INPUT → CONNECTOR → AGENT → OUTPUT`.
3. Show ordered business events and the structured result.
4. Export the immutable deterministic version and start its generic Docker runtime.
5. Invoke it without the control-plane API or worker and open the correlated MLflow trace.

The point is not the calculator. The demonstration proves that the same versioned declaration can
run durably in the product and independently through the portable runtime.

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
