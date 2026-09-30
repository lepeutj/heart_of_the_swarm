# Node development guide

This is the shortest path through the files involved in a workflow node. Start here before changing
node behavior or its visual representation.

## End-to-end path

```text
React Flow node
  frontend/src/workflow.ts
  frontend/src/components/NodeInspector.tsx
        ↓ JSON
WorkflowSpec
  src/heart_of_the_swarm/workflows/spec.py
  src/heart_of_the_swarm/workflows/configs.py
        ↓ validation
ValidatedWorkflowSpec
  src/heart_of_the_swarm/workflows/validation/validator.py
        ↓ translation
LangGraph StateGraph
  src/heart_of_the_swarm/workflows/execution/graph_factory.py
        ↓ node operation
  src/heart_of_the_swarm/workflows/execution/node_runner.py
```

React Flow edits data; it never defines runtime behavior. `WorkflowGraphFactory` creates the
LangGraph graph and delegates product-specific operations to `WorkflowNodeRunner`. LangGraph owns
traversal and routing.

## Current node ownership

| Node | Declarative configuration | Runtime representation |
| --- | --- | --- |
| `input` | Empty config | LangGraph entry node; validates workflow input |
| `llm` | Inline `AgentSpec` plus input/output paths | `AgentRunner` → LangChain `create_agent` |
| `agent` | Immutable `agent_version_id` plus paths | Version resolver → `AgentRunner` → `create_agent` |
| `transform` | Restricted state assignments | Small deterministic product adapter |
| `condition` | Conditions stored on ordered outgoing edges | LangGraph conditional edges |
| `output` | Output state path | LangGraph terminal node; validates output |

There is no standalone `tool` node. Tools belong to `AgentSpec.tools`, are resolved by the central
`ToolRegistry`, and are passed to LangChain when the agent is created.

## LangChain agent path

```text
LLM node: embedded AgentSpec ─┐
                             ├→ WorkflowNodeRunner._invoke_agent
AGENT node: AgentVersion ─────┘              ↓
                                      AgentRunner.invoke
                                             ↓
                      ProviderRegistry.create_model + AgentFactory.create
                                             ↓
                              langchain.agents.create_agent
```

Relevant files:

- `src/heart_of_the_swarm/spec.py`: shared `AgentSpec` and model configuration.
- `src/heart_of_the_swarm/agent_runtime.py`: validates, resolves, invokes, and extracts the answer.
- `src/heart_of_the_swarm/factory.py`: the only `create_agent` construction point.
- `src/heart_of_the_swarm/providers/`: model-provider adapters.
- `src/heart_of_the_swarm/tools/registry.py`: allow-listed tool registry.
- `src/heart_of_the_swarm/workflows/execution/agent_versions.py`: persistence-independent saved
  agent resolver contract.

Do not add a second model/tool loop inside a workflow node. Extend `AgentSpec`, provider resolution,
or `AgentFactory` when the capability belongs to LangChain agent construction.

## Files to change for node work

### Change an existing node configuration

1. Update its Pydantic model in `workflows/configs.py`.
2. Update semantic checks in `workflows/validation/validator.py`.
3. Update the operation in `workflows/execution/node_runner.py` only if runtime behavior changes.
4. Update `frontend/src/workflow.ts` defaults and types.
5. Update the typed form in `frontend/src/components/NodeInspector.tsx`.
6. Update backend and frontend tests.

### Add a node type

Also update:

- `workflows/enums.py`: public node identifier.
- `NODE_CONFIG_TYPES` in `workflows/configs.py`: configuration parser.
- `workflows/catalogue.py`: API capability description.
- `WorkflowGraphFactory.create`: LangGraph edge semantics, only when different from a linear node.
- `frontend/src/graph.ts`: only if insertion or detachment rules differ.

Before adding a type, identify its native LangGraph/LangChain representation. The product should
store only the configuration needed to reconstruct that representation.

## Persistence and editing

- `workflows/documents.py`: mutable draft and editor-layout API models.
- `workflow_service.py`: save rules and strict version publication.
- `models.py` and `repository.py`: workflow draft/version storage.
- `frontend/src/App.tsx`: save, reopen, version, and graph reconstruction.

A draft may be incomplete. A `WorkflowVersion` must parse and validate completely. Layout stays in
`WorkflowEditorDocument` and must never affect execution.

## Tests to start from

- `tests/test_workflow_validation.py`: schema, graph, and semantic rules.
- `tests/test_workflow_graph_factory.py`: WorkflowSpec-to-LangGraph translation.
- `tests/test_workflow_llm_execution.py`: inline agent delegation.
- `tests/test_workflow_agent_execution.py`: saved-version delegation.
- `tests/test_workflow_execution.py`: input, transform, condition, and output behavior.
- `tests/test_workflow_documents.py`: drafts and immutable versions.
- `frontend/src/workflow.test.ts` and `frontend/src/graph.test.ts`: serialization and graph editing.

## Safe change checklist

- Keep public configuration JSON-serializable.
- Keep React Flow and LangGraph objects out of `WorkflowSpec`.
- Validate before constructing runtime objects.
- Delegate traversal, retries, checkpoints, interrupts, streaming, and parallelism to LangGraph.
- Delegate model/tool loops and tool calling to LangChain.
- Add regression tests before removing superseded behavior.
- Update `docs/workflow-spec.md` and `docs/decisions.md` when the public contract changes.
