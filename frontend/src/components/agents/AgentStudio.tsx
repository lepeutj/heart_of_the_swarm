import { useEffect, useMemo, useState } from "react";

import {
  addAgentVersion,
  createAgent,
  designAgent,
  loadAgent,
  loadAgents,
  loadProviderNames,
  loadSkillNames,
  loadTools,
  validateAgent,
  type AgentDetail,
  type AgentOption,
  type AgentSpec,
  type ToolCapability,
} from "../../api";
import type { JsonObject } from "../../workflow";
import { ModelEditor } from "../ModelEditor";

function emptyAgent(provider = ""): AgentSpec {
  return {
    name: "NewAgent",
    goal: "Complete the assigned task accurately.",
    instructions: "Explain the result clearly and use tools only when they help complete the task.",
    model: {
      provider,
      model_id: "",
      temperature: 0,
      max_tokens: null,
    },
    tools: [],
    skills: [],
  };
}

function toggleName(values: string[], name: string): string[] {
  return values.includes(name) ? values.filter((value) => value !== name) : [...values, name];
}

export function AgentStudio({ onLogout }: { onLogout: () => void }) {
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [loadedAgent, setLoadedAgent] = useState<AgentDetail | null>(null);
  const [spec, setSpec] = useState<AgentSpec>(() => emptyAgent());
  const [tools, setTools] = useState<ToolCapability[]>([]);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [providerNames, setProviderNames] = useState<string[]>([]);
  const [designTask, setDesignTask] = useState("");
  const [designTraceId, setDesignTraceId] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [status, setStatus] = useState("Loading agent catalogues…");
  const [statusError, setStatusError] = useState(false);
  const [busy, setBusy] = useState(false);

  function reportStatus(message: string, isError = false) {
    setStatus(message);
    setStatusError(isError);
  }

  useEffect(() => {
    let active = true;
    Promise.all([loadAgents(), loadTools(), loadSkillNames(), loadProviderNames()])
      .then(([loadedAgents, loadedTools, loadedSkills, loadedProviders]) => {
        if (!active) return;
        setAgents(loadedAgents);
        setTools(loadedTools);
        setSkillNames(loadedSkills);
        setProviderNames(loadedProviders);
        setSpec((current) => current.model.provider
          ? current
          : emptyAgent(loadedProviders[0] ?? ""));
        reportStatus("Agent Studio ready");
      })
      .catch((error: unknown) => {
        if (active) {
          reportStatus(error instanceof Error ? error.message : "Could not load Agent Studio", true);
        }
      });
    return () => { active = false; };
  }, []);

  const unavailableTools = useMemo(
    () => spec.tools.filter((name) => !tools.some((tool) => tool.id === name)),
    [spec.tools, tools],
  );
  const unavailableSkills = useMemo(
    () => spec.skills.filter((name) => !skillNames.includes(name)),
    [skillNames, spec.skills],
  );

  function update<K extends keyof AgentSpec>(field: K, value: AgentSpec[K]) {
    setSpec((current) => ({ ...current, [field]: value }));
    setErrors([]);
  }

  function startNewAgent() {
    setSelectedAgentId("");
    setLoadedAgent(null);
    setSpec(emptyAgent(providerNames[0] ?? ""));
    setDesignTask("");
    setDesignTraceId(null);
    setErrors([]);
    reportStatus("New unsaved agent");
  }

  async function selectAgent(agentId: string) {
    if (!agentId) {
      startNewAgent();
      return;
    }
    setBusy(true);
    reportStatus("Loading agent…");
    try {
      const agent = await loadAgent(agentId);
      setSelectedAgentId(agent.id);
      setLoadedAgent(agent);
      setSpec(agent.spec);
      setDesignTraceId(null);
      setErrors([]);
      reportStatus(`Loaded ${agent.name} version ${agent.version}`);
    } catch (error) {
      reportStatus(error instanceof Error ? error.message : "Could not load agent", true);
    } finally {
      setBusy(false);
    }
  }

  async function generateDraft() {
    if (!designTask.trim()) {
      setErrors(["Describe the agent you want to create before generating a draft."]);
      reportStatus("Agent description is required", true);
      return;
    }
    setBusy(true);
    setErrors([]);
    reportStatus("Generating AgentSpec…");
    try {
      const result = await designAgent(
        designTask.trim(),
        typeof spec.model.provider === "string" ? spec.model.provider : undefined,
        typeof spec.model.model_id === "string" && spec.model.model_id
          ? spec.model.model_id
          : undefined,
      );
      setSpec(result.spec);
      setDesignTraceId(result.trace_id);
      reportStatus("Draft generated. Review it before validation and save.");
    } catch (error) {
      reportStatus(error instanceof Error ? error.message : "Agent generation failed", true);
    } finally {
      setBusy(false);
    }
  }

  async function validateDraft(): Promise<boolean> {
    setBusy(true);
    setErrors([]);
    reportStatus("Validating AgentSpec…");
    try {
      const result = await validateAgent(spec);
      setErrors(result.errors);
      reportStatus(
        result.valid ? "AgentSpec is valid" : `${result.errors.length} validation issue(s)`,
        !result.valid,
      );
      return result.valid;
    } catch (error) {
      reportStatus(error instanceof Error ? error.message : "Agent validation failed", true);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveVersion() {
    if (!await validateDraft()) return;
    setBusy(true);
    reportStatus(selectedAgentId ? "Creating immutable version…" : "Creating agent…");
    try {
      const saved = selectedAgentId
        ? await addAgentVersion(selectedAgentId, spec)
        : await createAgent(spec);
      setAgents(await loadAgents());
      setSelectedAgentId(saved.id);
      setLoadedAgent(saved);
      setSpec(saved.spec);
      reportStatus(`${saved.name} version ${saved.version} saved`);
    } catch (error) {
      reportStatus(error instanceof Error ? error.message : "Agent save failed", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell agent-studio-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Heart of the Swarm</p>
          <h1>Agent Studio</h1>
        </div>
        <nav className="topbar-actions" aria-label="Product navigation">
          <a className="active" href="/agents/">Agent Studio</a>
          <a href="/workflow-editor/">Workflow Studio</a>
          <button type="button" onClick={onLogout}>Sign out</button>
          <button type="button" disabled={busy} onClick={validateDraft}>Validate</button>
          <button type="button" className="primary" disabled={busy} onClick={saveVersion}>
            {selectedAgentId ? "Create new version" : "Save agent"}
          </button>
        </nav>
      </header>

      <main className="agent-studio">
        <aside className="agent-library">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Build</p>
              <h2>Agents</h2>
            </div>
            <button type="button" onClick={startNewAgent}>New</button>
          </div>
          <p className="field-help">
            Select the latest immutable version to review it or create the next version.
          </p>
          <div className="agent-list">
            {agents.map((agent) => (
              <button
                type="button"
                key={agent.id}
                className={selectedAgentId === agent.id ? "selected" : ""}
                onClick={() => selectAgent(agent.id)}
              >
                <strong>{agent.name}</strong>
                <span>Version {agent.version}</span>
              </button>
            ))}
            {!agents.length && <p className="muted">No saved agent yet.</p>}
          </div>
          <section className="agent-help-card">
            <strong>Workflow composition</strong>
            <p>
              Saved versions automatically become selectable from AGENT nodes in Workflow Studio.
            </p>
            <a href="/workflow-editor/">Open Workflow Studio →</a>
          </section>
        </aside>

        <section className="agent-definition">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Define</p>
              <h2>{loadedAgent ? `${loadedAgent.name} · v${loadedAgent.version}` : "New agent"}</h2>
            </div>
            <span className="version-badge">
              {loadedAgent ? "Editing creates a new version" : "Unsaved draft"}
            </span>
          </div>

          <section className="agent-builder-card">
            <h3>Generate a draft with AI</h3>
            <p className="field-help">
              Describe the behavior you need. Generated configuration remains an editable draft.
            </p>
            <textarea
              value={designTask}
              placeholder="Create a research agent that compares several sources and returns a concise cited summary."
              onChange={(event) => setDesignTask(event.target.value)}
            />
            <button type="button" disabled={busy} onClick={generateDraft}>Generate AgentSpec</button>
            {designTraceId && <small>Builder trace: {designTraceId}</small>}
          </section>

          <div className="agent-form-grid">
            <label>
              Name
              <input value={spec.name} onChange={(event) => update("name", event.target.value)} />
              <span className="field-help">Letters, numbers, underscores, and hyphens only.</span>
            </label>
            <label>
              Goal
              <input value={spec.goal} onChange={(event) => update("goal", event.target.value)} />
            </label>
          </div>
          <label>
            Instructions
            <textarea
              className="agent-instructions"
              value={spec.instructions}
              onChange={(event) => update("instructions", event.target.value)}
            />
          </label>

          <fieldset className="agent-config-section">
            <legend>Model</legend>
            <ModelEditor
              model={spec.model as JsonObject}
              providerNames={providerNames}
              requiresTools={spec.tools.length > 0}
              requiresStructuredOutput={false}
              onChange={(model) => update("model", model)}
            />
          </fieldset>

          <div className="agent-capability-grid">
            <fieldset className="agent-config-section">
              <legend>Tools</legend>
              <p className="field-help">The model may choose any selected capability during a run.</p>
              <div className="capability-checks">
                {tools.map((tool) => (
                  <label key={tool.id} title={tool.description}>
                    <input
                      type="checkbox"
                      checked={spec.tools.includes(tool.id)}
                      onChange={() => update("tools", toggleName(spec.tools, tool.id))}
                    />
                    <span><strong>{tool.id}</strong><small>{tool.source}</small></span>
                  </label>
                ))}
                {!tools.length && <p className="muted">No tool available.</p>}
              </div>
              {unavailableTools.length > 0 && (
                <p className="field-error">Unavailable: {unavailableTools.join(", ")}</p>
              )}
            </fieldset>

            <fieldset className="agent-config-section">
              <legend>Skills</legend>
              <p className="field-help">Markdown instructions are added to the agent context.</p>
              <div className="capability-checks">
                {skillNames.map((skill) => (
                  <label key={skill}>
                    <input
                      type="checkbox"
                      checked={spec.skills.includes(skill)}
                      onChange={() => update("skills", toggleName(spec.skills, skill))}
                    />
                    <span><strong>{skill}</strong><small>Markdown Skill</small></span>
                  </label>
                ))}
                {!skillNames.length && <p className="muted">No Skill uploaded yet.</p>}
              </div>
              {unavailableSkills.length > 0 && (
                <p className="field-error">Unavailable: {unavailableSkills.join(", ")}</p>
              )}
            </fieldset>
          </div>
        </section>

        <aside className="agent-review">
          <p className="eyebrow">Review</p>
          <h2>AgentSpec</h2>
          <p className={statusError ? "status error" : "status"}>{status}</p>
          {errors.map((error, index) => (
            <article className="issue" key={`${error}-${index}`}>
              <strong>Validation</strong>
              <span>{error}</span>
            </article>
          ))}
          <dl className="workflow-facts">
            <div><dt>Version</dt><dd>{loadedAgent?.version ?? "Draft"}</dd></div>
            <div><dt>Tools</dt><dd>{spec.tools.length}</dd></div>
            <div><dt>Skills</dt><dd>{spec.skills.length}</dd></div>
            <div><dt>Runtime</dt><dd>LangChain</dd></div>
          </dl>
          <details open>
            <summary>Declarative configuration</summary>
            <pre>{JSON.stringify(spec, null, 2)}</pre>
          </details>
          <p className="field-help">
            Saving stores configuration and a rendered prompt. Models and tools are rebuilt by the
            trusted runtime; no executable object enters AgentSpec.
          </p>
        </aside>
      </main>
    </div>
  );
}
