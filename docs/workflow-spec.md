# WorkflowSpec

`WorkflowSpec` is the portable executable graph edited by React and interpreted by LangGraph.
It contains no layout, database records, framework objects, credentials, or executable source.

Schema v1 remains frozen and acyclic. Schema v2 currently adds one bounded conditional back edge.
Other V2 contracts are tracked in [`v2-runtime-semantics.md`](v2-runtime-semantics.md).

## Nodes

| Type | Contract | Runtime |
| --- | --- | --- |
| `input` | Initialize and validate object input | Product adapter |
| `agent` | Inline `AgentSpec` or immutable `AgentVersion`, named mappings | `AgentRunner` / `create_agent` |
| `connector` | One registered capability invocation, named mappings | Capability registry |
| `transform` | Restricted state assignments | Product adapter |
| `condition` | Ordered conditional routes and one fallback | LangGraph routing |
| `output` | Named workflow output mappings | Product adapter |

Agent tools are selected by the model. Connectors deterministically invoke a capability once. Both
use the same allow-listed registry implementations. Built-in tools and MCP-discovered tools are
indistinguishable to workflow configuration and execution.

Connector arguments may be literals or state references. Capability input schemas are catalogue
metadata used by the editor and by LangChain validation. MCP content and structured artifacts are
normalized once at the registry boundary before declared output fields are projected into state.

## Data flow

Edges describe execution flow. Named mappings describe data flow through shared LangGraph state.
Agent nodes can read several state values and write several response fields without adding visual
ports to the canvas:

```yaml
inputs:
  question: {from_state: $.request}
  documents: {from_state: $.documents}
outputs:
  answer: {to_state: $.research.answer}
  sources: {to_state: $.research.sources}
response_schema:
  title: ResearchResult
  description: Structured research fields.
  type: object
  properties:
    answer: {type: string}
    sources: {type: array, items: {type: string}}
  required: [answer, sources]
```

One unstructured output may contain normal assistant text. Multiple outputs require a valid object
JSON Schema. The schema is delegated to LangChain structured output and the returned object is
validated before its named fields are written atomically into state. Legacy `input_path` and
`output_path` configurations remain executable during migration.

Multiple mappings do not enable parallel execution. Exclusive condition routes may converge, but
parallel fan-out, fan-in, and concurrent writes require explicit LangGraph reducers later.

An output node always projects named values. The workflow `output_schema` is optional; when present,
the runtime validates the complete projection before returning it.

## Safe expressions

State paths use only dot-separated identifiers such as `$.request` or `$.research.summary`.
Transforms may use literals or `{ "from_state": "$.path" }`. Conditions use the closed operator
set `equals`, `not_equals`, `exists`, `not_exists`, `contains`, `greater_than`, and `less_than`.
Arbitrary Python, templates, JSONPath filters, callbacks, and imports are forbidden.

Condition routes are evaluated in edge order. The first match wins; exactly one unconditional edge
is the fallback. Routes from one condition must have distinct targets.

## Bounded loop edge (schema v2)

A loop is metadata on a conditional back edge, not a node or a custom traversal engine:

```yaml
- source: review
  target: output
  condition: {path: $.approved, operator: equals, value: true}

- source: review
  target: draft
  loop: {id: revision, max_iterations: 3}
```

The first runtime accepts exactly one loop edge. Its source must be a condition, removing it must
leave a DAG, and its target must reach its source through forward edges. The iteration counter is
runtime-owned state, persists in LangGraph checkpoints, and exceeding the bound raises
`workflow.execution.iteration_limit`. Nested and overlapping loops remain unsupported.

## Validation and execution

```text
already-loaded WorkflowVersion
    ↓ WorkflowVersionRunner
shared input validation + WorkflowValidator
    ↓ WorkflowGraphFactory
LangGraph StateGraph
```

Validation checks structure, invocation input, typed node configuration, registered providers and
tools, graph rules, safe references, and JSON Schemas. The same input validator is used before a
durable run is queued and inside the runner. `WorkflowGraphFactory` accepts validated workflows
only. `WorkflowVersionRunner` receives an already-loaded immutable version and has no persistence
responsibility. LangGraph owns traversal and routing; product nodes only adapt declared behavior.

## Drafts, layout, and versions

A mutable workflow draft stores:

```text
workflow draft JSON      intended executable meaning
WorkflowEditorDocument  positions and viewport
revision                 optimistic concurrency
```

Saving preserves structurally or semantically incomplete editor work. Creating a `WorkflowVersion`
requires that JSON to parse as `WorkflowSpec`, then validates the workflow and each inline
`AgentSpec`. Versions are immutable. Opening a draft reconstructs the graph from its JSON plus the
separate editor document.
