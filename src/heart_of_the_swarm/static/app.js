const $ = (id) => document.getElementById(id);

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail?.message || body.detail || response.statusText);
  }
  return response.json();
}

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "";
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function metric(label, value, detail) {
  const card = element("article", "metric");
  card.append(element("span", "metric-label", label), element("strong", "metric-value", value));
  if (detail) card.append(element("small", "metric-detail", detail));
  return card;
}

function renderDefinitions(agents, workflows) {
  $("agent-count").textContent = agents.length;
  $("workflow-count").textContent = workflows.length;

  const agentList = $("agents");
  agentList.className = agents.length ? "definition-list" : "definition-list empty";
  agentList.replaceChildren(...agents.map((agent) => {
    const item = element("article", "definition");
    const heading = element("div", "definition-heading");
    heading.append(element("strong", "", agent.name), element("span", "badge", `v${agent.version}`));
    item.append(heading, element("p", "", agent.goal));
    return item;
  }));
  if (!agents.length) agentList.textContent = "No saved agents";

  const workflowList = $("workflows");
  workflowList.className = workflows.length ? "definition-list" : "definition-list empty";
  workflowList.replaceChildren(...workflows.map((workflow) => {
    const item = element("a", "definition");
    item.href = "/workflow-editor/";
    const heading = element("div", "definition-heading");
    heading.append(
      element("strong", "", workflow.name),
      element("span", "badge", workflow.latest_version ? `v${workflow.latest_version}` : "draft"),
    );
    item.append(heading, element("p", "", workflow.description || "No description"));
    return item;
  }));
  if (!workflows.length) workflowList.textContent = "No saved workflows";
}

function renderRuns(runs, agentsById) {
  const container = $("runs");
  container.className = runs.length ? "run-list" : "run-list empty";
  container.replaceChildren(...runs.slice(0, 20).map((run) => {
    const button = element("button", "run-row");
    const identity = element("span", "run-identity");
    identity.append(
      element("strong", "", agentsById.get(run.agent_id)?.name || "Agent"),
      element("small", "", new Date(run.queued_at).toLocaleString()),
    );
    button.append(identity, element("span", `run-status ${run.status}`, run.status));
    button.addEventListener("click", () => inspectRun(run, agentsById.get(run.agent_id)));
    return button;
  }));
  if (!runs.length) container.textContent = "No runs recorded";
}

async function inspectRun(run, agent) {
  const container = $("run-detail");
  container.className = "empty-state";
  container.textContent = "Loading trajectory…";
  try {
    const trajectory = await api(`/api/v1/runs/${run.run_id}/trajectory`);
    container.className = "run-inspection";
    container.replaceChildren();

    const metadata = element("dl", "run-metadata");
    for (const [label, value] of [
      ["Agent", agent?.name || run.agent_id],
      ["Status", run.status],
      ["Attempt", String(run.attempt)],
      ["Trace", run.trace_id],
    ]) {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      metadata.append(row);
    }
    container.append(metadata);
    if (run.error) container.append(element("p", "error-message", run.error));
    container.append(
      element("h3", "", "Output"),
      element("pre", "", run.output || "No output recorded."),
      element("h3", "", `Trajectory · ${trajectory.length} steps`),
      element("pre", "", trajectory.length
        ? JSON.stringify(trajectory, null, 2)
        : "No model or tool steps were captured."),
    );
  } catch (error) {
    container.className = "error-message";
    container.textContent = error.message;
  }
}

function renderUsage(rows) {
  $("usage").replaceChildren(...rows.map((row) => {
    const tr = document.createElement("tr");
    [row.provider, row.model_id, row.stage, row.calls, row.failures, row.total_tokens,
      row.cost.toFixed(6), `${row.average_latency_ms.toFixed(0)} ms`]
      .forEach((value) => tr.append(element("td", "", value)));
    return tr;
  }));
}

async function loadDashboard() {
  setStatus("Refreshing…");
  try {
    const [agents, workflows, usage] = await Promise.all([
      api("/api/v1/agents"),
      api("/api/v1/workflows"),
      api("/api/v1/usage/summary"),
    ]);
    const runs = (await Promise.all(
      agents.map((agent) => api(`/api/v1/agents/${agent.id}/runs`)),
    )).flat().sort((left, right) => new Date(right.queued_at) - new Date(left.queued_at));
    const completed = runs.filter((run) => run.status === "completed").length;
    const failed = runs.filter((run) => ["failed", "timed_out", "cancelled"].includes(run.status)).length;
    const tokens = usage.reduce((total, row) => total + row.total_tokens, 0);
    const cost = usage.reduce((total, row) => total + row.cost, 0);

    $("metrics").replaceChildren(
      metric("Agents", agents.length, "latest immutable versions"),
      metric("Workflows", workflows.length, "drafts and published graphs"),
      metric("Successful runs", completed, `${runs.length} total recorded`),
      metric("Failed runs", failed, failed ? "inspection recommended" : "no recorded failures"),
      metric("Model usage", tokens.toLocaleString(), `$${cost.toFixed(4)} tracked cost`),
    );
    renderDefinitions(agents, workflows);
    renderRuns(runs, new Map(agents.map((agent) => [agent.id, agent])));
    renderUsage(usage);
    setStatus(`Updated ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

$("refresh").addEventListener("click", loadDashboard);
loadDashboard();
