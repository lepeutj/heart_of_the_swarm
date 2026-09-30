import { useEffect, useState } from "react";
import { JsonEditor } from "./JsonEditor";
import { defaultConfig, type EditorNode, type JsonObject } from "../workflow";

interface NodeInspectorProps {
  node: EditorNode;
  toolNames: string[];
  providerNames: string[];
  canDetach: boolean;
  onChange: (update: Partial<EditorNode["data"]>) => void;
  onDetach: () => void;
  onDelete: () => void;
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
  providerNames,
  onChange,
}: {
  config: JsonObject;
  toolNames: string[];
  providerNames: string[];
  onChange: (config: JsonObject) => void;
}) {
  const agent = asObject(config.agent);
  const saved = agent.type === "saved";
  const spec = asObject(agent.spec);
  const model = asObject(spec.model);
  const selectedTools = Array.isArray(spec.tools)
    ? spec.tools.filter((tool): tool is string => typeof tool === "string")
    : [];

  function updateAgent(nextAgent: JsonObject) {
    onChange({ ...config, agent: nextAgent });
  }

  function updateSpec(field: string, value: unknown) {
    updateAgent({ ...agent, spec: { ...spec, [field]: value } });
  }

  function updateModel(field: string, value: unknown) {
    updateSpec("model", { ...model, [field]: value });
  }

  return (
    <div className="typed-editor">
      <label>
        Agent source
        <select
          value={saved ? "saved" : "inline"}
          onChange={(event) => {
            if (event.target.value === "saved") {
              updateAgent({ type: "saved", agent_version_id: "" });
            } else {
              updateAgent(asObject(defaultConfig("agent").agent));
            }
          }}
        >
          <option value="inline">Inline configuration</option>
          <option value="saved">Saved immutable version</option>
        </select>
      </label>

      {saved ? (
        <label>
          Agent version ID
          <input
            value={asString(agent.agent_version_id)}
            onChange={(event) => updateAgent({ ...agent, agent_version_id: event.target.value })}
          />
        </label>
      ) : (
        <>
          <label>Name<input value={asString(spec.name)} onChange={(event) => updateSpec("name", event.target.value)} /></label>
          <label>Goal<textarea value={asString(spec.goal)} onChange={(event) => updateSpec("goal", event.target.value)} /></label>
          <label>Instructions<textarea value={asString(spec.instructions)} onChange={(event) => updateSpec("instructions", event.target.value)} /></label>
          <ModelEditor model={model} providerNames={providerNames} onChange={updateModel} />
          <fieldset>
            <legend>Allowed tools</legend>
            <p className="field-help">The agent may choose among these tools during its LangChain loop.</p>
            <div className="tool-checks">
              {toolNames.map((tool) => (
                <label key={tool}>
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(tool)}
                    onChange={(event) => updateSpec(
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
        </>
      )}

      <label>Input path<input value={asString(config.input_path)} onChange={(event) => onChange({ ...config, input_path: event.target.value })} /></label>
      <label>Output path<input value={asString(config.output_path)} onChange={(event) => onChange({ ...config, output_path: event.target.value })} /></label>
    </div>
  );
}

function ToolEditor({ config, toolNames, onChange }: {
  config: JsonObject;
  toolNames: string[];
  onChange: (config: JsonObject) => void;
}) {
  return (
    <div className="typed-editor">
      <p className="field-help">A tool call executes exactly once. It does not give a model permission to choose the tool.</p>
      <label>
        Registered tool
        <select value={asString(config.tool)} onChange={(event) => onChange({ ...config, tool: event.target.value })}>
          <option value="">Select a tool</option>
          {toolNames.map((tool) => <option key={tool} value={tool}>{tool}</option>)}
        </select>
      </label>
      <JsonEditor
        label="Arguments"
        value={asObject(config.arguments)}
        onApply={(nextArguments) => nextArguments && onChange({
          ...config,
          arguments: nextArguments,
        })}
      />
      <label>Output path<input value={asString(config.output_path)} onChange={(event) => onChange({ ...config, output_path: event.target.value })} /></label>
    </div>
  );
}

function LlmEditor({ config, providerNames, onChange }: {
  config: JsonObject;
  providerNames: string[];
  onChange: (config: JsonObject) => void;
}) {
  const model = asObject(config.model);

  function updateModel(field: string, value: unknown) {
    onChange({ ...config, model: { ...model, [field]: value } });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">One model call with no tools or autonomous loop.</p>
      <label>Instruction<textarea value={asString(config.prompt)} onChange={(event) => onChange({ ...config, prompt: event.target.value })} /></label>
      <ModelEditor model={model} providerNames={providerNames} onChange={updateModel} />
      <label>Input path<input value={asString(config.input_path)} onChange={(event) => onChange({ ...config, input_path: event.target.value })} /></label>
      <label>Output path<input value={asString(config.output_path)} onChange={(event) => onChange({ ...config, output_path: event.target.value })} /></label>
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

export function NodeInspector({
  node,
  toolNames,
  providerNames,
  canDetach,
  onChange,
  onDetach,
  onDelete,
}: NodeInspectorProps) {
  const config = node.data.config;

  return (
    <div>
      <p className="selection-kind">Node · {node.data.nodeType}</p>
      <label>ID<input value={node.id} readOnly /></label>
      <label>Name<input value={node.data.label} onChange={(event) => onChange({ label: event.target.value })} /></label>

      {node.data.nodeType === "agent" && (
        <AgentEditor config={config} toolNames={toolNames} providerNames={providerNames} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "tool" && (
        <ToolEditor config={config} toolNames={toolNames} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "llm" && (
        <LlmEditor config={config} providerNames={providerNames} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "transform" && (
        <TransformEditor config={config} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "output" && (
        <label>Output path<input value={asString(config.output_path)} onChange={(event) => onChange({ config: { ...config, output_path: event.target.value } })} /></label>
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
        <button type="button" disabled={!canDetach} onClick={onDetach}>Detach and reconnect</button>
        <button type="button" className="danger" onClick={onDelete}>Delete node</button>
      </div>
      {!canDetach && !["input", "output"].includes(node.data.nodeType) && (
        <small className="field-help">Detaching is available only for a simple node with one incoming and one outgoing edge.</small>
      )}
    </div>
  );
}
