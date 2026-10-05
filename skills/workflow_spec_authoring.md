# WorkflowSpec Authoring

Design executable Heart of the Swarm workflows as declarative JSON. Return data matching the
requested structured-output schema, never Markdown or executable source code.

## Workflow contract

A workflow contains `schema_version`, a UUID `id`, `name`, `description`, object JSON Schemas for
its input and optional output, an `entrypoint`, `nodes`, and `edges`. Use schema version `1`.

Supported node types are:

- `input`: initializes and validates invocation data; its config is empty.
- `agent`: runs one inline AgentSpec or one immutable saved AgentVersion.
- `connector`: invokes exactly one registered capability without model choice.
- `transform`: copies state values or writes literals through restricted assignments.
- `condition`: selects one ordered route; configure conditions on outgoing edges.
- `output`: names and selects the final values returned by the workflow.

Edges define execution order. State mappings define data flow. State paths use only `$.` followed
by dot-separated identifiers. Never use Python, expressions, templates, callbacks, imports, or
arbitrary JSONPath.

Version 1 is an acyclic sequential graph. Do not generate parallel branches, joins, loops, retries,
human approval, or unsupported node types. A condition may select one exclusive branch and those
branches may converge later.

## Inline agents

An inline agent node uses:

```json
{
  "source": {
    "type": "inline",
    "agent": {
      "name": "ValidIdentifier",
      "goal": "One clear outcome.",
      "instructions": "Operational instructions.",
      "model": {
        "provider": "openrouter",
        "model_id": "qwen/qwen3.8-27b:free",
        "temperature": 0,
        "max_tokens": 3000
      },
      "tools": [],
      "skills": []
    }
  },
  "inputs": {
    "request": {"from_state": "$.request"}
  },
  "outputs": {
    "answer": {"to_state": "$.answer"}
  }
}
```

Use only these built-in capability identifiers when they are necessary:

- `calculator`: arithmetic expressions.
- `document_reader`: public HTML, text, or XML documents.
- `http_get_json`: public JSON endpoints.
- `rss_reader`: bounded normalized RSS or Atom entries.
- `web_search`: public web search.

Use only the `openrouter` provider and `qwen/qwen3.8-27b:free` model in generated examples.
Do not invent tools, Skills, providers, models, version IDs, credentials, or API keys.

One textual agent result needs no `response_schema`. Multiple named outputs require an object JSON
Schema whose properties are required. Map each returned property to shared state with `to_state`.

## Example: one summarization agent

```json
{
  "schema_version": "1",
  "id": "bf57442b-f46b-44db-a312-e60ebf70b526",
  "name": "Text summary",
  "description": "Summarize user-provided text.",
  "input_schema": {
    "type": "object",
    "properties": {"request": {"type": "string"}},
    "required": ["request"],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
    "additionalProperties": false
  },
  "entrypoint": "input",
  "nodes": [
    {"id": "input", "type": "input", "name": "Input", "config": {}},
    {
      "id": "summarize",
      "type": "agent",
      "name": "Summarizer",
      "config": {
        "source": {
          "type": "inline",
          "agent": {
            "name": "TextSummarizer",
            "goal": "Summarize supplied text.",
            "instructions": "Return a concise, factual summary using only supplied text.",
            "model": {
              "provider": "openrouter",
              "model_id": "qwen/qwen3.8-27b:free",
              "temperature": 0,
              "max_tokens": 1000
            },
            "tools": [],
            "skills": []
          }
        },
        "inputs": {"request": {"from_state": "$.request"}},
        "outputs": {"answer": {"to_state": "$.answer"}}
      }
    },
    {
      "id": "output",
      "type": "output",
      "name": "Output",
      "config": {"outputs": {"result": {"from_state": "$.answer"}}}
    }
  ],
  "edges": [
    {"source": "input", "target": "summarize"},
    {"source": "summarize", "target": "output"}
  ]
}
```

## Example: deterministic feed retrieval followed by analysis

Use a connector when retrieval must always occur, then give its state result to an agent:

```json
{
  "id": "read_feed",
  "type": "connector",
  "name": "Read feed",
  "config": {
    "capability_id": "rss_reader",
    "inputs": {
      "url": {"from_state": "$.feed_url"},
      "max_items": 5
    },
    "outputs": {
      "items": {"to_state": "$.feed.items"}
    }
  }
}
```

The next agent reads `$.feed.items`. The output node exposes only explicitly named final values.

## Final checks

Before returning a WorkflowSpec, verify that node IDs are unique, the entrypoint exists, every edge
references existing nodes, all non-output nodes lead toward an output, state mappings agree with
upstream values, final mappings agree with `output_schema`, and every inline AgentSpec uses only
the allowed model and capabilities above.
