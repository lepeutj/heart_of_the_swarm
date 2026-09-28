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
