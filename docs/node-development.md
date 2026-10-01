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
| `agent` | Inline `AgentSpec` or immutable version plus mappings | `AgentRunner` → `create_agent` |
| `connector` | Registered capability plus mappings | One direct capability invocation |
| `transform` | Restricted state assignments | Small deterministic product adapter |
| `condition` | Conditions stored on ordered outgoing edges | LangGraph conditional edges |
| `output` | Output state path | LangGraph terminal node; validates output |

Agent tools belong to `AgentSpec.tools`. A connector reuses the same registered implementation but
invokes it exactly once without model choice.

Built-in and discovered MCP capabilities live in the same `ToolRegistry`. MCP discovery registers
LangChain tools during application startup with source and server-origin metadata. `CONNECTOR` and
`AgentFactory` resolve only capability IDs and must not branch on their source. Name collisions are
startup errors rather than implicit replacements.

## LangChain agent path

```text
AGENT inline: embedded AgentSpec ─┐
                                  ├→ WorkflowNodeRunner._invoke_agent
AGENT version: AgentVersion ──────┘              ↓
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
- `models.py` and `repositories/workflows.py`: workflow draft/version storage.
- `frontend/src/App.tsx`: save, reopen, version, and graph reconstruction.

A draft may be incomplete. A `WorkflowVersion` must parse and validate completely. Layout stays in
`WorkflowEditorDocument` and must never affect execution.

## Tests to start from

- `tests/test_workflow_validation.py`: schema, graph, and semantic rules.
- `tests/test_workflow_graph_factory.py`: WorkflowSpec-to-LangGraph translation.
- `tests/test_workflow_llm_execution.py`: inline agent delegation.
- `tests/test_workflow_agent_execution.py`: saved-version delegation.
- `tests/test_workflow_connectors.py`: sequential deterministic capability execution.
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
