# WorkflowSpec v1

`WorkflowSpec` is the framework-independent contract for deterministic agent workflows. LangGraph
will compile it and the future visual editor will edit it; neither defines the language.

## Scope

Version 1 supports these node types:

- `input`
- `agent`
- `llm`
- `tool`
- `condition`
- `transform`
- `output`

Workflows are directed acyclic graphs. Approval, parallel branches, merges, loops, subworkflows,
memory, HTTP, and Python nodes are intentionally deferred.

## Contract

```json
{
  "schema_version": "1",
  "id": "4fc4fba0-f38c-4b03-95a4-d478f4636273",
  "name": "Research workflow",
  "description": "Collect and verify information.",
  "input_schema": {"type": "object"},
  "output_schema": {"type": "object"},
  "entrypoint": "input",
  "nodes": [
    {"id": "input", "type": "input", "name": "Input", "config": {}},
    {
      "id": "read",
      "type": "tool",
      "name": "Read page",
      "config": {
        "tool": "document_reader",
        "arguments": {"url": {"from_state": "$.url"}},
        "output_path": "$.document"
      }
    },
    {
      "id": "output",
      "type": "output",
      "name": "Output",
      "config": {"output_path": "$.document"}
    }
  ],
  "edges": [
    {"source": "input", "target": "read"},
    {"source": "read", "target": "output"}
  ]
}
```

`schema_version` versions the workflow language. A future `workflow_version` will version saved
user workflows; the two concepts are independent.

## State paths and references

Paths use a restricted syntax: `$.` followed by dot-separated identifiers. Arrays, filters,
predicates, recursive selectors, functions, and arbitrary expressions are not supported.

```text
$.request
$.research.sources
$.verification.valid
```

A value can refer to state explicitly:

```json
{"from_state": "$.request.url"}
```

All other JSON values are literals. Transforms and tool arguments share the same resolver.

## Conditions

Only condition nodes can have conditioned outgoing edges. A condition node requires at least one
conditional edge and exactly one unconditional fallback. Conditions are evaluated in edge order;
the first match wins, otherwise the fallback is selected.

Supported operators are `equals`, `not_equals`, `exists`, `not_exists`, `contains`,
`greater_than`, and `less_than`.

```json
{
  "source": "verified",
  "target": "success",
  "condition": {
    "path": "$.verification.valid",
    "operator": "equals",
    "value": true
  }
}
```

Conditions are data. They never execute Python, templates, or model-generated code.

## Validation

Validation parses every generic node configuration into a typed internal configuration and then
checks structure, node configuration, graph rules, and workflow semantics. Errors contain a stable
code, message, node ID when applicable, and field path so a future editor can highlight the exact
problem.

The validator rejects unknown tools and providers, invalid JSON Schemas, cycles, unreachable nodes,
paths that cannot reach an output, invalid condition routing, invalid state-path syntax, and invalid
node configuration. It deliberately does not try to prove that dynamically produced state values
will exist at runtime.

## Compilation

`WorkflowCompiler` accepts only `ValidatedWorkflowSpec` and produces a serializable, framework-
independent `ExecutionPlan`. The plan preserves workflow metadata, schemas, typed node configuration,
dependencies, ordered condition routes, fallback targets, and a stable topological execution order.

The current compiler supports `input`, `tool`, `transform`, `condition`, and `output`. It explicitly
rejects validated workflows containing `agent` or `llm` nodes until those runtime contracts are
implemented. Compilation stores only the registered tool identifier and typed configuration; it does
not store executable tool implementations, execute nodes, or repeat workflow validation.

## Deterministic execution

`WorkflowExecutor` accepts an `ExecutionPlan` and an object input. It copies that input into isolated
workflow state, follows control flow from the entrypoint, and executes only the selected path.

- `input` validates the initial state against `input_schema`.
- `tool` resolves literal and state-derived arguments, invokes one registered tool exactly once,
  and writes its result to the configured output path.
- `transform` resolves every assignment value from the same pre-transform state snapshot, then
  applies the resolved values to a copied state.
- `condition` evaluates routes in declared order and selects the first match or the fallback.
- `output` resolves its configured state path and validates the value against `output_schema`.

Successful execution returns `ExecutionResult` with the workflow ID, output, final state, and ordered
executed-node IDs. Failures raise a structured issue containing a stable code plus workflow, node,
node-type, and optional tool context. Tool argument schemas are enforced by the registered tool.
Registered tools that convert validation or execution exceptions into normal return values are
rejected because workflow failures must remain distinguishable from successful tool output. There
are no automatic tool retries or model decisions.

The async `aexecute` method supports synchronous and asynchronous tools. The synchronous `execute`
adapter is available when no event loop is running.

When the existing runtime callback is supplied, every node emits `node.started` and either
`node.completed` or `node.failed`. Registered tool invocation continues to emit the existing
`tool.started`, `tool.completed`, and `tool.failed` events. Tool events inherit workflow ID, workflow
run ID, node ID, and node type through callback metadata.
