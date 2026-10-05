# Roadmap

## Product flow

```text
Build visually or with the Builder LLM
→ validate
→ save draft
→ create immutable version
→ test and trace
→ deploy
```

## Completed foundation

- Shared `AgentSpec`, LangChain `create_agent`, OpenAI/OpenRouter providers, and tool registry.
- Immutable agent versions, worker runs, trajectories, usage metrics, and Docker stack.
- `WorkflowSpec` validation and direct LangGraph `StateGraph` translation.
- React Flow editor with typed inspectors, edge insertion, detachment, and ordered conditions.
- Unified `AGENT` nodes backed by inline `AgentSpec` data or immutable `AgentVersion` references.
- Markdown Skills injected into agent context and deterministic registered connectors.
- Mutable workflow drafts with separate layout metadata and immutable workflow versions.
- Dynamic capability registration and MCP discovery through the shared tool registry.
- Real MCP-to-connector execution, structured result normalization, isolated server failures, and
  schema-driven connector arguments.
- Persisted MCP HTTP sources with add/test/refresh UI, provider-safe namespacing, and immutable
  capability contracts on published workflow versions.
- Durable workflow-version runs through the worker, ordered node lifecycle events, bounded
  LangGraph execution, and editor polling/result display.
- Shared synchronous workflow-input validation and a persistence-independent
  `WorkflowVersionRunner` for already-loaded immutable versions.
- Typed manual, webhook, and interval-schedule trigger contracts persisted against immutable agent
  or workflow versions.
- External workflow webhooks that create validated durable runs and preserve trigger origin.
- Exported workflow-version artifacts and a generic standalone workflow runtime API.
- Optional MLflow LangChain/LangGraph autotracing with runtime trace-ID correlation.
- Real Docker verification of an exported workflow artifact and its nested MLflow trace.

## Immediate order

1. Complete the V1 demonstration and final verification.
2. Freeze the release boundary and avoid new product capabilities.

## Later

- LangGraph checkpoints, interrupts, retries, streaming, parallel branches, and bounded loops.
- Schedule claiming, payload mapping, agent trigger targets, and trigger UI.
- Deployment lifecycle, local Docker instances, then one Docker/VPS target.
- Authentication, permissions, quotas, retention, and additional deployment providers.

Do not add UI-only execution concepts, custom agent loops, custom workflow traversal, or duplicate
tracing/evaluation platforms.
