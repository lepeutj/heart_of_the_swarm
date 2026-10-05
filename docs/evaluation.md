# Evaluation and observability

Execution answers "what happened?" Evaluation answers "did the agent perform the task correctly and
efficiently?" Heart of the Swarm governs product identity and lifecycle; MLflow is the primary
technical tracing and evaluation backend.

## Trace ownership

```text
LangChain / LangGraph
        ↓
MLflow autotracing
        ↓
technical spans, model calls, tool calls, latency, tokens, and costs

Heart of the Swarm
        ↓
PostgreSQL
        ↓
versions, run status, deployment lifecycle, audit events, and MLflow trace reference
```

Manual instrumentation is limited to product information that framework autotracing does not know:

- `agent_id` and `agent_version_id`;
- `workflow_id` and `workflow_version_id`;
- `deployment_id`;
- `run_id`;
- workflow `node_id` where applicable;
- the correlation between product and MLflow trace identifiers.

The existing callback and PostgreSQL trajectory tables remain during migration. After parity is
verified, they retain only business events required by the product and stop duplicating complete
model messages, tool payloads, and framework spans.

## Evaluation layers

Use MLflow capabilities before adding product-specific infrastructure:

### Deterministic scorers

- Execution success and structured-output compliance.
- Exact or bounded values.
- Required and forbidden tool calls.
- Expected fields, files, or citations.
- Latency, token, cost, and retry thresholds.

### Task-specific scorers

Examples include classification accuracy, retrieval precision, citation validity, code-test
success, and domain-specific correctness.

### Model-based scorers

Use explicit criteria when deterministic evaluation is insufficient. Model judges are measurements,
not ground truth, and should be calibrated against representative examples when needed.

### Human review

Human feedback supports subjective quality, edge cases, scorer calibration, and regression
investigation. A dedicated human-approval workflow is separate from evaluation and waits for durable
LangGraph pause/resume support.

## Datasets and experiments

Do not build a generic dataset, experiment-comparison, or LLM-judge platform inside Heart of the
Swarm. MLflow owns technical datasets, evaluation runs, scores, and experiment comparison.

Heart of the Swarm may provide product-facing views and exports that attach immutable agent or
workflow versions to MLflow datasets and experiments. Any local domain object should contain only
the product relationship and configuration that MLflow does not own.

## Evaluation sequence

```text
immutable AgentVersion
        ↓
representative inputs
        ↓
LangChain/LangGraph execution
        ↓
MLflow traces
        ↓
deterministic and model-based scorers
        ↓
version comparison
        ↓
deployment decision
```

Workflow evaluation follows the same model after the agent product and LangGraph workflow runtime
are complete.
