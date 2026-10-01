# Heart of the Swarm

Heart of the Swarm designs, validates, saves, versions, and runs configurable LangGraph agents.
It includes a small web interface, OpenAI and OpenRouter providers, PostgreSQL persistence,
structured audit logs, model-usage aggregation, and optional MLflow traces.

## Docker pack

Copy the environment file, add at least one provider key, and start the stack:

```bash
cp .env.example .env
docker compose up --build
```

On Windows PowerShell, use `Copy-Item .env.example .env`.

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

Build and run the generic runtime image with the artifact mounted read-only:

```bash
docker build -f docker/agent-runtime.Dockerfile -t heart-of-the-swarm-agent-runtime .
docker run --rm -p 8080:8000 \
  --mount type=bind,source="$(pwd)/artifact",target=/app/artifact,readonly \
  -e OPENAI_API_KEY \
  heart-of-the-swarm-agent-runtime
```

Use `OPENROUTER_API_KEY` instead for an OpenRouter artifact. The running container exposes only
`GET /health`, `GET /metadata`, and `POST /invoke`. It does not connect to PostgreSQL, the control
plane API, or the worker.

```bash
curl http://localhost:8080/health
curl http://localhost:8080/metadata
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"input":"Complete the assigned task."}'
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

## API

```text
GET  /api/v1/providers
GET  /api/v1/models?provider=openrouter
GET  /api/v1/tools
GET  /api/v1/workflows/capabilities
GET  /api/v1/workflows
GET  /api/v1/workflows/{id}
PUT  /api/v1/workflows/{id}
POST /api/v1/workflows/{id}/versions
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

The draft is returned for review and is not executed automatically. Starting a run returns
`202 Accepted` with a run ID. The UI polls the run resource while the worker executes the pinned
agent version.

## Runtime architecture

The API is the control plane: it designs and versions agents, validates requests, and queues runs.
The worker is the execution plane: it claims queued runs and persists their lifecycle. It delegates
the framework invocation to a persistence-independent `AgentRunner`, which validates the saved
specification, resolves the model, builds the LangChain agent, and returns its final response. Both
processes use the same application image and PostgreSQL database.

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

The workflow contract defines input, inline tool-enabled LLM, saved agent, transform, condition,
and output nodes. Inline LLM configuration is an `AgentSpec` executed through `create_agent`; saved
agent nodes reference immutable versions. LangGraph remains the only orchestration engine.
Workflow drafts store executable specifications separately from React Flow layout and publish into
immutable validated versions.

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

```powershell
Get-Content .\logs\api.jsonl -Wait
Get-Content .\logs\worker.jsonl -Wait
Select-String -Path .\logs\*.jsonl -Pattern '<trace-id>'
```

Prompt and document contents are not written to the audit log. Prompts and run content are stored
in PostgreSQL and MLflow for reproducibility, so access to both systems must be restricted.

## Local development without Docker

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required. SQLite is used when
`DATABASE_URL` is not set. `uv sync` creates the project environment and installs the locked
application and development dependencies.

```bash
uv sync --locked
cp .env.example .env
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

The UI is intentionally functional rather than visually final. Cancellation is cooperative: a
model request already in progress finishes before the worker observes it. Authentication,
role-based access, hard execution timeouts, prompt redaction, retention policies, and production
network controls remain future work. Do not expose the current stack publicly without
authentication and rate limits.
