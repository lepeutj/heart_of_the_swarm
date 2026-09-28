const $ = (id) => document.getElementById(id);
let currentAgentId = null;
let currentRunId = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail?.message || body.detail || response.statusText);
  }
  return response.json();
}

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").style.color = error ? "#ffb4b4" : "#b8d4c2";
}

async function loadProviders() {
  const providers = await api("/api/v1/providers");
  $("provider").replaceChildren(...providers.map((provider) => {
    const option = new Option(provider.configured ? provider.id : `${provider.id} (not configured)`, provider.id);
    option.disabled = !provider.configured;
    return option;
  }));
  await loadModels();
}

async function loadModels() {
  const provider = $("provider").value;
  if (!provider) return;
  const models = await api(`/api/v1/models?provider=${encodeURIComponent(provider)}`);
  $("model").replaceChildren(...models.map((model) => {
    const capabilities = model.supports_tools ? " · tools" : "";
    return new Option(`${model.name}${capabilities}`, model.model_id);
  }));
}

async function loadTools() {
  const { tools } = await api("/api/v1/tools");
  $("tools").replaceChildren(...tools.map((tool) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = tool;
    label.append(input, tool);
    return label;
  }));
}

function readSpec() {
  return {
    name: $("name").value,
    goal: $("goal").value,
    tools: [...$("tools").querySelectorAll("input:checked")].map((input) => input.value),
    instructions: $("instructions").value,
    model: {
      provider: $("provider").value,
      model_id: $("model").value,
      temperature: Number($("temperature").value),
      max_tokens: null,
    },
  };
}

async function fillSpec(spec, systemPrompt = "") {
  $("name").value = spec.name;
  $("goal").value = spec.goal;
  $("instructions").value = spec.instructions;
  $("system-prompt").value = systemPrompt;
  $("temperature").value = spec.model.temperature;
  $("provider").value = spec.model.provider;
  await loadModels();
  $("model").value = spec.model.model_id;
  $("tools").querySelectorAll("input").forEach((input) => {
    input.checked = spec.tools.includes(input.value);
  });
}

async function design() {
  setStatus("Designing…");
  try {
    const result = await api("/api/v1/agents/design", {
      method: "POST",
      body: JSON.stringify({
        task: $("task").value,
        provider: $("provider").value,
        model_id: $("model").value,
      }),
    });
    await fillSpec(result.spec);
    currentAgentId = null;
    $("selected-agent").textContent = "Unsaved draft";
    $("run").disabled = true;
    setStatus(`Draft ready · ${result.trace_id.slice(0, 8)}`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function validate() {
  const result = await api("/api/v1/agents/validate", {
    method: "POST",
    body: JSON.stringify(readSpec()),
  });
  $("validation").className = `message ${result.valid ? "success" : "error"}`;
  $("validation").textContent = result.valid ? "Configuration is valid." : result.errors.join(" · ");
  return result.valid;
}

async function save() {
  if (!(await validate())) return;
  const path = currentAgentId ? `/api/v1/agents/${currentAgentId}/versions` : "/api/v1/agents";
  const agent = await api(path, { method: "POST", body: JSON.stringify(readSpec()) });
  selectAgent(agent);
  await loadAgents();
  setStatus("Agent saved");
}

function selectAgent(agent) {
  currentAgentId = agent.id;
  $("selected-agent").textContent = `${agent.name} · version ${agent.version}`;
  $("run").disabled = false;
  if (agent.spec) fillSpec(agent.spec, agent.system_prompt);
}

async function loadAgents() {
  const agents = await api("/api/v1/agents");
  const container = $("agents");
  container.className = agents.length ? "list" : "list empty";
  container.replaceChildren(...agents.map((agent) => {
    const button = document.createElement("button");
    button.textContent = `${agent.name} · v${agent.version}`;
    button.onclick = async () => selectAgent(await api(`/api/v1/agents/${agent.id}`));
    return button;
  }));
  if (!agents.length) container.textContent = "No saved agents";
}

async function run() {
  if (!currentAgentId) return;
  setStatus("Queuing run…");
  $("output").textContent = "Waiting for a worker…";
  try {
    const result = await api(`/api/v1/agents/${currentAgentId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input: $("run-input").value }),
    });
    currentRunId = result.run_id;
    $("cancel-run").disabled = false;
    $("trajectory").textContent = "Capturing trajectory…";
    await waitForRun(result.run_id);
    await loadUsage();
  } catch (error) {
    $("output").textContent = error.message;
    setStatus(error.message, true);
  } finally {
    currentRunId = null;
    $("cancel-run").disabled = true;
  }
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForRun(runId) {
  while (true) {
    const result = await api(`/api/v1/runs/${runId}`);
    setStatus(`Run ${result.status} · attempt ${result.attempt}`);
    if (result.status === "completed") {
      await loadTrajectory(runId);
      $("output").textContent = `${result.output}\n\nTrace: ${result.trace_id}`;
      setStatus("Run complete");
      return;
    }
    if (["failed", "cancelled", "timed_out"].includes(result.status)) {
      await loadTrajectory(runId);
      throw new Error(result.error || `Run ${result.status}`);
    }
    await sleep(1000);
  }
}

async function loadTrajectory(runId) {
  const steps = await api(`/api/v1/runs/${runId}/trajectory`);
  $("trajectory").textContent = steps.length
    ? JSON.stringify(steps, null, 2)
    : "No observable model or tool steps were captured.";
}

async function cancelRun() {
  if (!currentRunId) return;
  await api(`/api/v1/runs/${currentRunId}/cancel`, { method: "POST" });
  setStatus("Cancellation requested");
}

async function loadUsage() {
  const rows = await api("/api/v1/usage/summary");
  $("usage").replaceChildren(...rows.map((row) => {
    const tr = document.createElement("tr");
    [row.provider, row.model_id, row.stage, row.calls, row.failures, row.total_tokens,
      row.cost.toFixed(6), `${row.average_latency_ms.toFixed(0)} ms`]
      .forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.append(td); });
    return tr;
  }));
}

$("provider").addEventListener("change", () => loadModels().catch((error) => setStatus(error.message, true)));
$("design").addEventListener("click", design);
$("validate").addEventListener("click", () => validate().catch((error) => setStatus(error.message, true)));
$("save").addEventListener("click", () => save().catch((error) => setStatus(error.message, true)));
$("run").addEventListener("click", run);
$("cancel-run").addEventListener("click", () => cancelRun().catch((error) => setStatus(error.message, true)));

Promise.all([loadProviders(), loadTools(), loadAgents(), loadUsage()])
  .catch((error) => setStatus(error.message, true));
