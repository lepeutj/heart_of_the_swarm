# Repository instructions

Heart of the Swarm turns declarative agent and workflow configuration into tested, traced,
versioned, and deployable LangChain/LangGraph runtimes.

Before significant changes, read `PROJECT.md`, the active milestone in `docs/roadmap.md`, the
concise `docs/decisions.md` index, and only the contract sections relevant to the task. When local
planning files such as `tasks/current.md` or `reports/latest.md` are present, read those first to
avoid reopening completed work. Do not reread deferred sections without a concrete need, silently
replace accepted decisions, or redesign unrelated code.

## Language

Use English for code, tests, commits, and documentation. Communicate with the owner in French unless
asked otherwise.

## Product contracts

- `AgentSpec` is shared by the Builder LLM, UI, API, persistence, and runtime.
- `AgentVersion` and `WorkflowVersion` are immutable execution and deployment units.
- `WorkflowSpec` contains executable meaning; `WorkflowEditorDocument` contains layout only.
- Builder output is configuration, never Python, shell, imports, credentials, or callbacks.
- Models and tools resolve through trusted registries. MCP tools must use the same tool catalogue.
- Public specifications remain JSON-serializable and contain no runtime objects.

## Framework boundaries

- Use `langchain.agents.create_agent`; never implement another model/tool loop.
- Use LangGraph `StateGraph` for workflow orchestration and framework runtime features.
- Keep validation separate from execution and reject invalid states early.
- Keep persistence, FastAPI, React, and deployment as adapters around domain contracts.
- Do not put React Flow or LangGraph objects into public specifications.
- Remove superseded code after equivalent behavior is verified.
- Conditions and transforms use only the restricted language in `docs/workflow-spec.md`.

## Observability and deployment

- MLflow owns technical traces and evaluation; PostgreSQL owns product versions, lifecycle, audit,
  and MLflow trace references.
- Prefer framework autotracing and add only product metadata manually.
- Deploy generic runtimes plus immutable declarations; never generated services or serialized graphs.
- Inject secrets externally and never persist credentials in specifications.

## Development

- Use `uv`, never `pip install` instructions.
- Justify new dependencies and avoid overlapping libraries.
- Test meaningful behavior, invalid input, and important failures; every bug fix needs a regression
  test. External providers use fakes or mocks in unit tests.
- Never commit keys, tokens, passwords, private keys, or real credentials.

Required verification:

```text
uv run --locked python -m pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

Also verify frontend tests/build, dependency locks, and migrations when affected. For substantial
work, update stable documentation and architecture decisions. Update local reports and task files
when they are present.
