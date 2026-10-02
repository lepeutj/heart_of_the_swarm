import { useEffect, useState } from "react";

import type { AgentOption, ToolCapability } from "../api";
import type { EditorNode, JsonObject } from "../workflow";
import { AgentDataFlowEditor, WorkflowOutputEditor } from "./DataFlowEditor";
import { JsonEditor } from "./JsonEditor";

interface NodeInspectorProps {
  node: EditorNode;
  toolNames: string[];
  capabilities: ToolCapability[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  canDetach: boolean;
  onChange: (update: Partial<EditorNode["data"]>) => void;
  onDetach: () => void;
  onDelete: () => void;
  onSaveAsAgent: () => void;
}

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function ValueEditor({ value, onApply }: { value: unknown; onApply: (value: unknown) => void }) {
  const [text, setText] = useState(JSON.stringify(value));

  useEffect(() => setText(JSON.stringify(value)), [value]);

  return (
    <div className="inline-value-editor">
      <input value={text} onChange={(event) => setText(event.target.value)} />
      <button
        type="button"
        onClick={() => {
          try {
            onApply(JSON.parse(text));
          } catch {
            onApply(text);
          }
        }}
      >Apply</button>
    </div>
  );
}

function ModelEditor({
  model,
  providerNames,
  onChange,
}: {
  model: JsonObject;
  providerNames: string[];
  onChange: (field: string, value: unknown) => void;
}) {
  const provider = asString(model.provider);
  return (
    <>
      <label>
        Provider
        <select value={provider} onChange={(event) => onChange("provider", event.target.value)}>
          {!providerNames.includes(provider) && <option value={provider}>{provider}</option>}
          {providerNames.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      <label>Model ID<input value={asString(model.model_id)} onChange={(event) => onChange("model_id", event.target.value)} /></label>
      <div className="field-grid">
        <label>
          Temperature
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={typeof model.temperature === "number" ? model.temperature : 0}
            onChange={(event) => onChange("temperature", Number(event.target.value))}
          />
        </label>
        <label>
          Max tokens
          <input
            type="number"
            min="1"
            value={typeof model.max_tokens === "number" ? model.max_tokens : ""}
            onChange={(event) => onChange(
              "max_tokens",
              event.target.value ? Number(event.target.value) : null,
            )}
          />
        </label>
      </div>
    </>
  );
}

function AgentEditor({
  config,
  toolNames,
  skillNames,
  providerNames,
  agentOptions,
  onChange,
}: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  onChange: (config: JsonObject) => void;
}) {
  const source = asObject(config.source);
  const sourceType = asString(source.type, "inline");
  return (
    <div className="typed-editor">
      <label>
        Agent source
        <select
          value={sourceType}
          onChange={(event) => onChange({
            ...config,
            source: event.target.value === "version"
              ? { type: "version", agent_version_id: "" }
              : {
                  type: "inline",
                  agent: {
                    name: "NewAgent",
                    goal: "Complete the assigned task",
                    instructions: "Return a clear and accurate answer.",
                    model: { provider: "openai", model_id: "", temperature: 0, max_tokens: null },
                    tools: [],
                    skills: [],
                  },
                },
          })}
        >
          <option value="inline">Inline AgentSpec</option>
          <option value="version">Saved AgentVersion</option>
        </select>
      </label>
      {sourceType === "version" ? (
        <label>
          Saved agent
          <select
            value={asString(source.agent_version_id)}
            onChange={(event) => onChange({
              ...config,
              source: { type: "version", agent_version_id: event.target.value },
            })}
          >
            <option value="">Select an agent version</option>
            {agentOptions.map((agent) => (
              <option key={agent.version_id} value={agent.version_id}>
                {agent.name} · v{agent.version}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <InlineAgentEditor
          config={config}
          toolNames={toolNames}
          skillNames={skillNames}
          providerNames={providerNames}
          onChange={onChange}
        />
      )}
      {sourceType === "version" && <AgentDataFlowEditor config={config} onChange={onChange} />}
    </div>
  );
}

function InlineAgentEditor({ config, toolNames, skillNames, providerNames, onChange }: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  onChange: (config: JsonObject) => void;
}) {
  const source = asObject(config.source);
  const agent = asObject(source.agent);
  const model = asObject(agent.model);
  const selectedTools = Array.isArray(agent.tools)
    ? agent.tools.filter((tool): tool is string => typeof tool === "string")
    : [];

  function updateAgent(field: string, value: unknown) {
    onChange({
      ...config,
      source: { type: "inline", agent: { ...agent, [field]: value } },
    });
  }

  function updateModel(field: string, value: unknown) {
    updateAgent("model", { ...model, [field]: value });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">This inline AgentSpec is executed by LangChain and may call its allowed tools.</p>
      <label>Name<input value={asString(agent.name)} onChange={(event) => updateAgent("name", event.target.value)} /></label>
      <label>Goal<textarea value={asString(agent.goal)} onChange={(event) => updateAgent("goal", event.target.value)} /></label>
      <label>Instructions<textarea value={asString(agent.instructions)} onChange={(event) => updateAgent("instructions", event.target.value)} /></label>
      <ModelEditor model={model} providerNames={providerNames} onChange={updateModel} />
      <fieldset>
        <legend>Allowed tools</legend>
        <p className="field-help">The model chooses among these tools during its agent loop.</p>
        <div className="tool-checks">
          {toolNames.map((tool) => (
            <label key={tool}>
              <input
                type="checkbox"
                checked={selectedTools.includes(tool)}
                onChange={(event) => updateAgent(
                  "tools",
                  event.target.checked
                    ? [...selectedTools, tool]
                    : selectedTools.filter((selected) => selected !== tool),
                )}
              />
              {tool}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Skills</legend>
        {skillNames.map((skill) => {
          const selected = Array.isArray(agent.skills)
            ? agent.skills.filter((name): name is string => typeof name === "string")
            : [];
          return (
            <label key={skill}>
              <input
                type="checkbox"
                checked={selected.includes(skill)}
                onChange={(event) => updateAgent(
                  "skills",
                  event.target.checked
                    ? [...selected, skill]
                    : selected.filter((name) => name !== skill),
                )}
              />
              {skill}
            </label>
          );
        })}
      </fieldset>
      <AgentDataFlowEditor config={config} onChange={onChange} />
    </div>
  );
}

function MappingRow({
  destination,
  value,
  onRename,
  onChange,
  onRemove,
}: {
  destination: string;
  value: unknown;
  onRename: (destination: string) => void;
  onChange: (value: unknown) => void;
  onRemove: () => void;
}) {
  const [destinationDraft, setDestinationDraft] = useState(destination);
  const reference = asObject(value);
  const fromState = Object.keys(reference).length === 1 && typeof reference.from_state === "string";

  useEffect(() => setDestinationDraft(destination), [destination]);

  return (
    <div className="mapping-row">
      <label>
        Destination
        <input
          value={destinationDraft}
          onChange={(event) => setDestinationDraft(event.target.value)}
          onBlur={() => onRename(destinationDraft)}
        />
      </label>
      <label>
        Value type
        <select
          value={fromState ? "state" : "literal"}
          onChange={(event) => onChange(
            event.target.value === "state" ? { from_state: "$.request" } : "",
          )}
        >
          <option value="state">State reference</option>
          <option value="literal">Literal JSON</option>
        </select>
      </label>
      {fromState ? (
        <label>
          Source path
          <input
            value={asString(reference.from_state)}
            onChange={(event) => onChange({ from_state: event.target.value })}
          />
        </label>
      ) : (
        <label>Literal value<ValueEditor value={value} onApply={onChange} /></label>
      )}
      <button type="button" onClick={onRemove}>Remove mapping</button>
    </div>
  );
}

function TransformEditor({ config, onChange }: {
  config: JsonObject;
  onChange: (config: JsonObject) => void;
}) {
  const assignments = asObject(config.assign);

  function replace(assign: JsonObject) {
    onChange({ ...config, assign });
  }

  function rename(previous: string, destination: string) {
    if (!destination || (destination !== previous && destination in assignments)) return;
    const next = Object.fromEntries(
      Object.entries(assignments).map(([path, value]) => [
        path === previous ? destination : path,
        value,
      ]),
    );
    replace(next);
  }

  return (
    <div className="typed-editor">
      <p className="field-help">Map state values or set literals. Every source is read before any destination is written.</p>
      {Object.entries(assignments).map(([destination, value]) => (
        <MappingRow
          key={destination}
          destination={destination}
          value={value}
          onRename={(nextDestination) => rename(destination, nextDestination)}
          onChange={(nextValue) => replace({ ...assignments, [destination]: nextValue })}
          onRemove={() => {
            const next = { ...assignments };
            delete next[destination];
            replace(next);
          }}
        />
      ))}
      <button type="button" onClick={() => {
        let index = Object.keys(assignments).length + 1;
        while (`$.field_${index}` in assignments) index += 1;
        replace({ ...assignments, [`$.field_${index}`]: { from_state: "$.request" } });
      }}>Add mapping</button>
    </div>
  );
}

function schemaProperties(schema: Record<string, unknown>): Record<string, JsonObject> {
  const properties = schema.properties;
  if (typeof properties !== "object" || properties === null || Array.isArray(properties)) return {};
  return Object.fromEntries(
    Object.entries(properties).filter((entry): entry is [string, JsonObject] => (
      typeof entry[1] === "object" && entry[1] !== null && !Array.isArray(entry[1])
    )),
  );
}

function defaultLiteral(schema: JsonObject): unknown {
  if (schema.type === "number" || schema.type === "integer") return 0;
  if (schema.type === "boolean") return false;
  if (schema.type === "array") return [];
  if (schema.type === "object") return {};
  return "";
}

function ConnectorEditor({ config, capabilities, onChange }: {
  config: JsonObject;
  capabilities: ToolCapability[];
  onChange: (config: JsonObject) => void;
}) {
  const capabilityId = asString(config.capability_id);
  const capability = capabilities.find((item) => item.id === capabilityId);
  const inputs = asObject(config.inputs);
  const properties = schemaProperties(capability?.input_schema ?? {});
  const required = new Set(
    Array.isArray(capability?.input_schema.required)
      ? capability.input_schema.required.filter((name): name is string => typeof name === "string")
      : [],
  );

  function changeCapability(nextId: string) {
    const nextCapability = capabilities.find((item) => item.id === nextId);
    const nextProperties = schemaProperties(nextCapability?.input_schema ?? {});
    onChange({
      ...config,
      capability_id: nextId,
      inputs: Object.fromEntries(
        Object.entries(nextProperties)
          .filter(([name]) => (
            Array.isArray(nextCapability?.input_schema.required)
            && nextCapability.input_schema.required.includes(name)
          ))
          .map(([name]) => [name, { from_state: `$.${name}` }]),
      ),
    });
  }

  function setInput(name: string, value: unknown) {
    onChange({ ...config, inputs: { ...inputs, [name]: value } });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">Invoke one registered capability exactly once.</p>
      <label>
        Capability
        <select
          value={capabilityId}
          onChange={(event) => changeCapability(event.target.value)}
        >
          {!capability && capabilityId && <option value={capabilityId}>{capabilityId} · unavailable</option>}
          {capabilities.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}{item.source === "mcp" ? ` · ${item.origin}` : ""}
            </option>
          ))}
        </select>
      </label>
      {capability && <p className="field-help">{capability.description}</p>}
      {Object.keys(properties).length ? <fieldset>
        <legend>Arguments</legend>
        {Object.entries(properties).map(([name, schema]) => {
          const enabled = name in inputs;
          const value = inputs[name];
          const stateReference = asObject(value).from_state;
          const usesState = typeof stateReference === "string";
          return (
            <div className="mapping-row" key={name}>
              <label>
                {!required.has(name) && (
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(event) => {
                      if (event.target.checked) setInput(name, { from_state: `$.${name}` });
                      else {
                        const next = { ...inputs };
                        delete next[name];
                        onChange({ ...config, inputs: next });
                      }
                    }}
                  />
                )}
                {asString(schema.title, name)}{required.has(name) ? " *" : ""}
              </label>
              {enabled && (
                <>
                  <select
                    value={usesState ? "state" : "literal"}
                    onChange={(event) => setInput(
                      name,
                      event.target.value === "state"
                        ? { from_state: `$.${name}` }
                        : defaultLiteral(schema),
                    )}
                  >
                    <option value="state">State path</option>
                    <option value="literal">Literal</option>
                  </select>
                  {usesState ? (
                    <input
                      value={stateReference}
                      onChange={(event) => setInput(name, { from_state: event.target.value })}
                    />
                  ) : (
                    <ValueEditor value={value} onApply={(next) => setInput(name, next)} />
                  )}
                </>
              )}
            </div>
          );
        })}
      </fieldset> : (
        <JsonEditor
          label="Arguments (schema unavailable)"
          value={inputs}
          onApply={(value) => value && onChange({ ...config, inputs: value })}
        />
      )}
      <JsonEditor
        label="Outputs (result field to state path)"
        value={asObject(config.outputs)}
        onApply={(value) => value && onChange({ ...config, outputs: value })}
      />
    </div>
  );
}

export function NodeInspector({
  node,
  toolNames,
  capabilities,
  skillNames,
  providerNames,
  agentOptions,
  canDetach,
  onChange,
  onDetach,
  onDelete,
  onSaveAsAgent,
}: NodeInspectorProps) {
  const config = node.data.config;

  return (
    <div>
      <p className="selection-kind">Node · {node.data.nodeType}</p>
      <label>ID<input value={node.id} readOnly /></label>
      <label>Name<input value={node.data.label} onChange={(event) => onChange({ label: event.target.value })} /></label>

      {node.data.nodeType === "agent" && (
        <AgentEditor
          config={config}
          toolNames={toolNames}
          skillNames={skillNames}
          providerNames={providerNames}
          agentOptions={agentOptions}
          onChange={(next) => onChange({ config: next })}
        />
      )}
      {node.data.nodeType === "connector" && (
        <ConnectorEditor
          config={config}
          capabilities={capabilities}
          onChange={(next) => onChange({ config: next })}
        />
      )}
      {node.data.nodeType === "transform" && (
        <TransformEditor config={config} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "output" && (
        <WorkflowOutputEditor config={config} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "condition" && (
        <p className="field-help">Select an outgoing edge to configure its condition. One outgoing edge must remain the fallback.</p>
      )}
      {node.data.nodeType === "input" && (
        <p className="field-help">The input node validates the initial state against the workflow input schema.</p>
      )}
      <details>
        <summary>Advanced configuration JSON</summary>
        <JsonEditor
          key={node.id}
          label="Configuration"
          value={config}
          onApply={(value) => value && onChange({ config: value })}
        />
      </details>

      <div className="node-actions">
        {node.data.nodeType === "agent" && asObject(config.source).type === "inline" && (
          <button type="button" onClick={onSaveAsAgent}>Save as agent</button>
        )}
        <button type="button" disabled={!canDetach} onClick={onDetach}>Detach and reconnect</button>
        <button type="button" className="danger" onClick={onDelete}>Delete node</button>
      </div>
      {!canDetach && !["input", "output"].includes(node.data.nodeType) && (
        <small className="field-help">Detaching is available only for a simple node with one incoming and one outgoing edge.</small>
      )}
    </div>
  );
}
