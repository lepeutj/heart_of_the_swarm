# Read-only PostgreSQL + MCP demonstration

This example demonstrates the complete capability boundary without giving an LLM arbitrary SQL:

```text
PostgreSQL demo data
  ↓ database-enforced read-only role
FastMCP business tools
  ↓ discovery into Heart's shared ToolRegistry
CONNECTOR or AGENT
  ↓
result, events, and MLflow trace
```

The MCP server exposes four bounded tools:

- `sales__database_access`
- `sales__sales_summary`
- `sales__top_products`
- `sales__recent_orders`

The database role has only `CONNECT`, schema `USAGE`, and table `SELECT`. It also defaults every
transaction to read-only. The MCP implementation uses fixed parameterized queries inside explicit
read-only transactions; it does not accept SQL from the model.

## Start the demonstration

Use the normal local `.env`, including a development auth token and an OpenRouter key. Also set two
URL-safe local values that remain outside Git:

```ini
SALES_DB_OWNER_PASSWORD=replace-with-a-local-demo-owner-password
SALES_DB_READER_PASSWORD=replace-with-a-local-demo-reader-password
```

Then run:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml up --build -d
```

The override starts `sales-db` and `sales-mcp`, and configures the Heart API and worker to discover
`http://sales-mcp:8001/mcp`. Private network access is enabled only in this demonstration override.
Open the Workflow Studio, expand **Skills and MCP resources**, and confirm that the `sales` source
is ready and its four tools appear in the catalogue.

Verify discovery, a live query, and the effective read-only identity through the same LangChain MCP
adapter used by Heart:

```bash
uv run --locked python examples/sales_mcp/smoke_test.py
```

## Verify database permissions

The positive check returns the role and its effective privileges:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml exec \
  sales-db psql -U heart_sales_reader -d sales_demo \
  -c "SELECT current_user, current_setting('transaction_read_only');"
```

The following negative check must fail with `permission denied for table orders`:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml exec \
  sales-db psql -U heart_sales_reader -d sales_demo \
  -c "DELETE FROM orders;"
```

The same commands work in PowerShell; put each command on one line or replace the backslashes with
PowerShell backticks.

## Use it from Heart

Two declarations are provided:

- `agent-spec.json` lets the model choose among the four MCP tools;
- `workflow-spec.json` invokes `sales__sales_summary` deterministically with a `CONNECTOR`, then
  gives the returned data to an inline reporting agent and pauses at `HUMAN_APPROVAL` before
  returning the report.

Heart uses default-deny runtime authorization. After creating an immutable agent or workflow
version, grant only the capability needed by its exact immutable principal and restart the worker.
For an agent version:

```ini
CAPABILITY_POLICIES={"agent_version:AGENT_VERSION_UUID":["sales__database_access","sales__sales_summary","sales__top_products","sales__recent_orders"]}
```

For the sample workflow's connector node:

```ini
CAPABILITY_POLICIES={"workflow_node:WORKFLOW_VERSION_UUID/fetch_sales":["sales__sales_summary"]}
```

This explicit restart is intentionally visible in the portfolio demo: the declaration requests
tools, but the immutable execution principal receives permissions independently. A future policy
administration UI may remove this manual step without changing the runtime contract.

Suggested agent tasks:

```text
Summarise revenue over the last 30 days and list the two best-selling products.
Verify whether your database connection can modify orders, then explain the result.
Show the five most recent orders and identify which still need processing.
```

The workflow input is simply:

```json
{"days": 30}
```

The approval is durable HITL, not a chat session: the worker checkpoints and releases the run,
Approve/Reject creates a continuation run, and completed database/model work is not replayed.

## Conversation is a separate product contract

Agent Studio currently executes one task as one independent `AgentRun`. Reusing the same immutable
version does not preserve conversational memory. A real conversation should not be implemented as
one LangGraph node waiting forever or as an unbounded graph loop.

The intended next contract is:

```text
AgentConversation (one immutable AgentVersion)
├── user message
│   └── bounded AgentRun #1 → assistant message
├── user message
│   └── bounded AgentRun #2 → assistant message
└── close / reactivate later
```

Messages remain visible product data. LangGraph may checkpoint the agent thread, but a worker is
active only while processing one turn. The conversation UI will send another message, follow that
run, and append the answer. This is distinct from `HUMAN_APPROVAL`, whose V2.5 contract intentionally
accepts one boolean decision.

## Verify the exported AgentVersion in an isolated runtime

Conversation support is not required for this milestone. The deployed container is long-lived and
may receive successive independent tasks, but each `/invoke` is intentionally stateless.

1. Create the agent from `agent-spec.json` in Agent Studio and run it locally.
2. Export that exact immutable version and extract the ZIP into
   `./output/sales-agent-artifact` so the directory directly contains `manifest.json` and
   `agent.json`.
3. Copy `agent_version_id` from `manifest.json` into `SALES_AGENT_VERSION_ID` in `.env` and set a
   local `SALES_RUNTIME_API_TOKEN`.
4. Start the generic runtime with the `remote-demo` profile:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml --profile remote-demo \
  up --build -d sales-agent-runtime
```

The artifact is mounted read-only. The runtime receives the four exact MCP permissions for that
immutable agent version and listens on `http://localhost:8080`.

Linux/macOS:

```bash
export TOKEN="your-local-runtime-service-token"
curl http://localhost:8080/health
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/metadata
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"input":"Summarise revenue over the last 30 days and list the two best products."}' \
  http://localhost:8080/invoke
```

PowerShell:

```powershell
$headers = @{ Authorization = "Bearer $env:SALES_RUNTIME_API_TOKEN" }
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/metadata -Headers $headers
Invoke-RestMethod -Method Post http://localhost:8080/invoke -Headers $headers `
  -ContentType 'application/json' `
  -Body (@{ input = 'Résume les ventes des 30 derniers jours.' } | ConvertTo-Json)
```

Use the returned `run_id` with authenticated `/runs/{run_id}` and `/runs/{run_id}/events`. The same
image, artifact directory, MCP configuration, and service token can then be copied to a Raspberry
Pi or VPS; only addresses and externally injected credentials change.

## Stop and reset

Stop services while retaining both databases:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml down
```

Delete only the demonstration sales data when a clean seed is needed:

```bash
docker compose -f compose.yaml -f compose.sales-demo.yaml down
docker volume rm heart_of_the_swarm_sales_demo_data
```

The last command is destructive only for the disposable demonstration database volume.
