# Heart of the Swarm

Heart of the Swarm designs, validates, versions, and runs LangChain agents and LangGraph workflows.
It includes a visual editor, OpenAI and OpenRouter providers, PostgreSQL persistence, structured
audit logs, model-usage aggregation, portable Docker runtimes, and optional MLflow traces.

For the frozen V1 architecture, its boundaries, and a reproducible five-minute demonstration, see
[`docs/v1-demo.md`](docs/v1-demo.md).

## Docker pack

Copy the environment file, add at least one provider key, and start the stack:
Commands that differ are shown for Linux/macOS first and Windows PowerShell second. Commands not
split by platform use the same syntax on both.

### Linux / macOS

```bash
cp .env.example .env
docker compose up --build
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
docker compose up --build
```

| Service | URL | Purpose |
| --- | --- | --- |
| Application | <http://localhost:8000> | Configuration UI and API |
| Workflow editor | <http://localhost:8000/workflow-editor/> | React Flow workflow configuration |
| API documentation | <http://localhost:8000/docs> | OpenAPI interface |
| MLflow | <http://localhost:5000> | Trace exploration |
| PostgreSQL | Internal only | Agents, versions, runs, and model usage |
| Worker | Internal only | Claims and executes queued agent runs |
| Migrations | One-shot | Applies the versioned database schema before startup |

PostgreSQL and MLflow artifacts use named Docker volumes. Application audit logs are written to
the host `logs` directory.

`/health` is a process liveness check. `/ready` verifies the database connection, required tables,
and expected Alembic revision; Docker uses readiness when deciding whether the API is healthy.

## Standalone agent runtime

Export one immutable agent version from the control-plane database:

```bash
uv run --locked swarm-export-agent <agent-version-uuid> ./artifact
```

The output contains only portable declarations:

```text
artifact/
├── manifest.json
└── agent.json
```

Build and run the generic runtime image with the artifact mounted read-only.

### Linux / macOS

```bash
docker build -f docker/agent-runtime.Dockerfile -t heart-of-the-swarm-agent-runtime .
docker run --rm -p 8080:8000 \
  --mount type=bind,source="$(pwd)/artifact",target=/app/artifact,readonly \
  -e OPENAI_API_KEY \
  heart-of-the-swarm-agent-runtime
```

### Windows PowerShell

```powershell
docker build -f docker/agent-runtime.Dockerfile -t heart-of-the-swarm-agent-runtime .
$artifact = (Resolve-Path .\artifact).Path
docker run --rm -p 8080:8000 `
  --mount "type=bind,source=$artifact,target=/app/artifact,readonly" `
  -e "OPENAI_API_KEY=$env:OPENAI_API_KEY" `
  heart-of-the-swarm-agent-runtime
```

Use `OPENROUTER_API_KEY` instead for an OpenRouter artifact. The running container exposes only
`GET /health`, `GET /metadata`, and `POST /invoke`. It does not connect to PostgreSQL, the control
plane API, or the worker.

### Linux / macOS

```bash
curl http://localhost:8080/health
curl http://localhost:8080/metadata
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"input":"Complete the assigned task."}'
```

### Windows PowerShell

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/metadata
Invoke-RestMethod -Method Post http://localhost:8080/invoke `
  -ContentType 'application/json' `
  -Body (@{ input = 'Complete the assigned task.' } | ConvertTo-Json)
```

## Agent workflow

```text
User intent -> LLM draft -> edit -> validate -> save/version -> queue -> worker -> result
```

The builder produces configuration, not executable code. Executable capabilities remain limited
to the server-side registry:

- `web_search`
- `calculator`
- `document_reader`
- `http_get_json`
- `database_query`
- `rss_reader`

## V1 reference demonstration: an agent creates an RSS agent

With the Docker stack running and `OPENROUTER_API_KEY` configured, run:

```bash
uv run --locked python examples/workflow_factory_demo.py
```

This demonstration uses only the public API. It creates and versions a workflow-designer agent,
asks that agent to generate a second workflow containing an inline RSS agent, validates and
versions the generated declaration, then runs it against a public technology feed. The final agent
autonomously calls `rss_reader` through LangChain before returning a French thematic summary.

```text
Designer agent
    ↓ generates a validated WorkflowSpec
INPUT(feed_url, request)
    ↓
Generated inline agent(tools=[rss_reader])
    ↓ reads and groups the latest entries
OUTPUT(result: French thematic summary)
```

The command prints every workflow, version, and run identifier. Open the workflow editor to inspect
the generated graph and MLflow at <http://localhost:5000> to verify the nested model → tool → model
trace. Free OpenRouter pools can return temporary HTTP 429 errors; the demonstration uses a small,
bounded number of separate factory and execution runs and keeps every durable run record for
inspection. This is demo-level resilience, not an implicit runtime retry of a workflow node.
Pass `--model-id <openrouter-model-id>` to test another model that supports both structured output
and tool calling. The deterministic regression version of this scenario is
`test_agent_can_generate_and_execute_another_rss_agent` in
`tests/test_workflow_end_to_end.py`; it uses a local RSS response and fake model messages while
exercising the real LangChain agent, tool registry, `rss_reader`, and LangGraph workflow runtime.

## Standalone workflow runtime

Export the immutable generated RSS workflow version printed by the reference demonstration:

```bash
uv run --locked swarm-export-workflow <workflow-version-uuid> ./workflow-artifact
```

The output contains `manifest.json` and `workflow.json`. Build and run the generic image.

### Linux / macOS

```bash
docker build -f docker/workflow-runtime.Dockerfile -t heart-of-the-swarm-workflow-runtime .
docker run --rm -p 8080:8000 \
  --mount type=bind,source="$(pwd)/workflow-artifact",target=/app/artifact,readonly \
  -e OPENROUTER_API_KEY \
  heart-of-the-swarm-workflow-runtime
```

### Windows PowerShell

```powershell
docker build -f docker/workflow-runtime.Dockerfile -t heart-of-the-swarm-workflow-runtime .
$artifact = (Resolve-Path .\workflow-artifact).Path
docker run --rm -p 8080:8000 `
  --mount "type=bind,source=$artifact,target=/app/artifact,readonly" `
  -e "OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY" `
  heart-of-the-swarm-workflow-runtime
```

Invoke it with the workflow's declared object input:

### Linux / macOS

```bash
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"input":{"feed_url":"https://feeds.bbci.co.uk/news/technology/rss.xml","request":"Résume en français les principaux thèmes des cinq dernières publications."}}'
```

### Windows PowerShell

```powershell
Invoke-RestMethod -Method Post http://localhost:8080/invoke `
  -ContentType 'application/json' `
  -Body (@{
    input = @{
      feed_url = 'https://feeds.bbci.co.uk/news/technology/rss.xml'
      request = 'Résume en français les principaux thèmes des cinq dernières publications.'
    }
  } | ConvertTo-Json -Depth 3)
```

The runtime reconstructs LangGraph from the declaration and uses the same portable runner as the
worker. It does not connect to PostgreSQL. V1 supports built-in capabilities and inline agents;
MCP capabilities, saved-agent references, and external Skill files are rejected during export.

Set `MLFLOW_ENABLED=true`, `MLFLOW_TRACKING_URI`, and `MLFLOW_EXPERIMENT` when starting either
standalone runtime to enable LangChain/LangGraph autotracing. `/invoke` then returns both the
product `trace_id` and `mlflow_trace_id`. If MLflow is unavailable, execution continues and the
MLflow identifier is omitted.

## API

```text
GET  /api/v1/providers
GET  /api/v1/models?provider=openrouter
GET  /api/v1/tools
GET  /api/v1/skills
POST /api/v1/skills
GET  /api/v1/mcp/servers
POST /api/v1/mcp/servers
POST /api/v1/mcp/servers/{id}/test
POST /api/v1/mcp/servers/{id}/refresh
GET  /api/v1/workflows/capabilities
GET  /api/v1/workflows
GET  /api/v1/workflows/{id}
PUT  /api/v1/workflows/{id}
POST /api/v1/workflows/{id}/versions
GET  /api/v1/workflows/{id}/versions/latest
POST /api/v1/workflow-versions/{id}/runs
GET  /api/v1/workflow-runs/{id}
GET  /api/v1/workflow-runs/{id}/events
POST /api/v1/triggers
GET  /api/v1/triggers
GET  /api/v1/triggers/{id}
POST /api/v1/hooks/{trigger_id}
GET  /health
GET  /ready
POST /api/v1/workflows/validate
POST /api/v1/agents/design
POST /api/v1/agents/validate
POST /api/v1/agents
POST /api/v1/agents/{id}/versions
GET  /api/v1/agents
GET  /api/v1/agents/{id}
POST /api/v1/agents/{id}/runs
GET  /api/v1/agents/{id}/runs
GET  /api/v1/runs/{id}
GET  /api/v1/runs/{id}/events
GET  /api/v1/runs/{id}/trajectory
POST /api/v1/runs/{id}/cancel
GET  /api/v1/usage/summary
```

Example design request:

```json
{
  "task": "Create an agent that compares product prices",
  "provider": "openrouter",
  "model_id": "openai/gpt-4.1-mini"
}
```

The draft is returned for review and is not executed automatically. Starting an agent or immutable
workflow-version run returns `202 Accepted` with a run ID. The UI polls the run resource while the
worker executes the pinned version.

## Runtime architecture

The API is the control plane: it designs and versions agents, validates requests, and queues runs.
Workflow input is checked against the immutable version before a run is created. The worker fairly
claims queued agent and workflow runs and persists their lifecycle. Agent runs delegate to
`AgentRunner`; workflow runs pass an already-loaded version to `WorkflowVersionRunner`, which is
independent from PostgreSQL and delegates traversal to LangGraph. Both processes currently use the
same application image and PostgreSQL database.

PostgreSQL is also the initial queue. Workers claim rows atomically, renew a lease while working,
and recover expired leases after a crash. More workers can be added without changing the API. A
future external queue can replace this repository boundary without changing the UI contract.

Agent versions store the rendered system prompt and its template version. Design sessions store
the original task, rendered builder prompt, builder model, generated specification, status, and
trace ID. Runs always reference the exact agent version they execute.

Each run also stores an ordered observable trajectory for every execution attempt. It includes
model requests and responses, tool inputs and outputs, errors, latency, and LangChain parent-child
run IDs. This captures the reproducible execution path, not private model reasoning. Trajectory
payloads can contain prompts, user data, and retrieved documents and must be protected accordingly.

## Workflow foundation

The workflow contract defines input, agent, connector, transform, condition, and output nodes. One
`AGENT` node either embeds an `AgentSpec` or references an immutable agent version; both execute
through `create_agent`. A `CONNECTOR` invokes one registered capability exactly once without model
choice. LangGraph remains the only orchestration engine.
Workflow drafts store executable specifications separately from React Flow layout and publish into
immutable validated versions. Published versions can be queued from the editor, bounded by timeout
and LangGraph recursion limits, followed through ordered workflow/node business events, and read
back with their structured output.

Workflow runs are not replayed automatically after a worker lease expires. Without durable
checkpoints and connector idempotency, replaying a partially executed graph could repeat external
side effects.

For the file-by-file path from a React node to LangGraph and LangChain, see
[`docs/node-development.md`](docs/node-development.md).

## Model usage

Every model call records:

- Builder or agent stage
- Provider and requested model
- Input, output, and total tokens
- Reported cost when supplied by the provider
- Latency and success status
- Application trace ID and agent run ID

`GET /api/v1/usage/summary` aggregates this data for the UI and future dashboards. When
`MLFLOW_ENABLED=true`, design and run spans include their inputs and outputs. The JSON audit log
remains available if MLflow is unavailable.

## Audit traces

Every request receives an `X-Trace-ID` header. The API and worker use separate structured JSON
Lines files so their rotating writers cannot conflict. Both rotate at 10 MB with five backups by
default.

### Linux / macOS

```bash
tail -f logs/api.jsonl logs/worker.jsonl
# In another terminal:
grep -F '<trace-id>' logs/*.jsonl
```

### Windows PowerShell

```powershell
Get-Content .\logs\api.jsonl, .\logs\worker.jsonl -Wait
# In another terminal:
Select-String -Path .\logs\*.jsonl -Pattern '<trace-id>'
```

Prompt and document contents are not written to the audit log. Prompts and run content are stored
in PostgreSQL and MLflow for reproducibility, so access to both systems must be restricted.

## Local development without Docker

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required. SQLite is used when
`DATABASE_URL` is not set. `uv sync` creates the project environment and installs the locked
application and development dependencies.

### Linux / macOS

```bash
uv sync --locked
cp .env.example .env
```

### Windows PowerShell

```powershell
uv sync --locked
Copy-Item .env.example .env
```

For local SQLite development, change the environment file to:

```ini
DATABASE_URL=sqlite+aiosqlite:///./data/heart_of_the_swarm.db
MLFLOW_ENABLED=false
```

Apply migrations once, and whenever a new migration is added:

```bash
uv run --locked alembic upgrade head
```

Run the API and worker in separate terminals:

```bash
uv run --locked uvicorn heart_of_the_swarm.api:app --reload
uv run --locked python -m heart_of_the_swarm.worker
```

The workflow editor is a separate Vite application. Its production build is served by FastAPI and
is generated rather than committed:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

For frontend development, run `pnpm dev` in `frontend`; Vite proxies `/api` to the local FastAPI
server. React Flow state is converted to the backend-owned `WorkflowSpec` before validation, and
the backend capability catalogue controls which node types the editor exposes as available.

## Tests

```bash
uv run --locked python -m pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

Frontend changes additionally require `pnpm test` and `pnpm build` from `frontend`.

## Current boundaries

The UI is intentionally functional rather than visually final. Agent cancellation is cooperative:
a model request already in progress finishes before the worker observes it. Workflow runs have a
hard timeout but do not yet support cancellation, checkpoints, resume, or human approval.
Authentication, role-based access, prompt redaction, retention policies, and production network
controls remain future work. Do not expose the current stack publicly without authentication and
rate limits.

The schedule trigger contract can be stored, but no scheduler claims due triggers in V1. Parallel
branches, durable checkpoints, retries, streaming, and human approval are also intentionally V2.
