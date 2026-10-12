import { useEffect, useState } from "react";

import type { AgentOption, ToolCapability, WorkflowVersion } from "../api";
import type { EditorNode, JsonObject, SupervisorTargetOption } from "../workflow";
import {
  AgentDataFlowEditor,
  StateInputEditor,
  WorkflowOutputEditor,
} from "./DataFlowEditor";
import { JsonEditor } from "./JsonEditor";
import { ModelEditor } from "./ModelEditor";
import { WorkflowSchemaEditor } from "./WorkflowSchemaEditor";

interface NodeInspectorProps {
  node: EditorNode;
  toolNames: string[];
  capabilities: ToolCapability[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  supervisorTargets: SupervisorTargetOption[];
  workflowVersions: WorkflowVersion[];
  canDetach: boolean;
  onChange: (update: Partial<EditorNode["data"]>) => void;
  onDetach: () => void;
  onDelete: () => void;
  onSaveAsAgent: () => void;
  inputSchema: JsonObject;
  outputSchema: JsonObject | null;
  onInputSchemaChange: (schema: JsonObject) => void;
  onOutputSchemaChange: (schema: JsonObject | null) => void;
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

function AgentSourceEditor({
  config,
  toolNames,
  skillNames,
  providerNames,
  agentOptions,
  requiresStructuredOutput,
  onChange,
}: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  requiresStructuredOutput: boolean;
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
                    model: {
                      provider: providerNames[0] ?? "openai",
                      model_id: "",
                      temperature: 0,
                      max_tokens: null,
                    },
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
          requiresStructuredOutput={requiresStructuredOutput}
          onChange={onChange}
        />
      )}
    </div>
  );
}

function InlineAgentEditor({
  config,
  toolNames,
  skillNames,
  providerNames,
  requiresStructuredOutput,
  onChange,
}: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  requiresStructuredOutput: boolean;
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

  return (
    <div className="typed-editor">
      <p className="field-help">This inline AgentSpec is executed by LangChain and may call its allowed tools.</p>
      <label>Name<input value={asString(agent.name)} onChange={(event) => updateAgent("name", event.target.value)} /></label>
      <label>Goal<textarea value={asString(agent.goal)} onChange={(event) => updateAgent("goal", event.target.value)} /></label>
      <label>Instructions<textarea value={asString(agent.instructions)} onChange={(event) => updateAgent("instructions", event.target.value)} /></label>
      <ModelEditor
        model={model}
        providerNames={providerNames}
        requiresTools={selectedTools.length > 0}
        requiresStructuredOutput={requiresStructuredOutput}
        onChange={(nextModel) => updateAgent("model", nextModel)}
      />
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
    </div>
  );
}

function AgentEditor(props: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  onChange: (config: JsonObject) => void;
}) {
  return (
    <>
      <AgentSourceEditor
        {...props}
        requiresStructuredOutput={props.config.response_schema !== null
          && props.config.response_schema !== undefined}
      />
      <AgentDataFlowEditor config={props.config} onChange={props.onChange} />
    </>
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

function SubworkflowEditor({ config, versions, onChange }: {
  config: JsonObject;
  versions: WorkflowVersion[];
  onChange: (config: JsonObject) => void;
}) {
  const versionId = asString(config.workflow_version_id);
  const inputs = asObject(config.inputs);
  const outputs = asObject(config.outputs);

  function selectVersion(nextId: string) {
    const version = versions.find((item) => item.id === nextId);
    if (!version) {
      onChange({ ...config, workflow_version_id: nextId });
      return;
    }
    const inputProperties = schemaProperties(version.spec.input_schema);
    const outputProperties = schemaProperties(version.spec.output_schema ?? {});
    onChange({
      ...config,
      workflow_version_id: nextId,
      inputs: Object.fromEntries(
        Object.keys(inputProperties).map((name) => [name, { from_state: `$.${name}` }]),
      ),
      outputs: Object.fromEntries(
        Object.keys(outputProperties).map((name) => [name, { to_state: `$.${name}` }]),
      ),
    });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">Run one immutable workflow version as an isolated LangGraph subgraph.</p>
      <label>
        Child workflow version
        <select value={versionId} onChange={(event) => selectVersion(event.target.value)}>
          <option value="">Select a published workflow</option>
          {versionId && !versions.some((item) => item.id === versionId) && (
            <option value={versionId}>{versionId} · unavailable</option>
          )}
          {versions.map((version) => (
            <option key={version.id} value={version.id}>
              {version.spec.name} · v{version.version}
            </option>
          ))}
        </select>
      </label>
      <fieldset>
        <legend>Child inputs</legend>
        <p className="field-help">Map parent state paths to fields accepted by the child workflow.</p>
        {Object.entries(inputs).map(([name, value]) => (
          <label key={name}>
            {name}
            <input
              value={asString(asObject(value).from_state)}
              onChange={(event) => onChange({
                ...config,
                inputs: { ...inputs, [name]: { from_state: event.target.value } },
              })}
            />
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Child outputs</legend>
        <p className="field-help">Write selected child result fields into parent state.</p>
        {Object.entries(outputs).map(([name, value]) => (
          <label key={name}>
            {name}
            <input
              value={asString(asObject(value).to_state)}
              onChange={(event) => onChange({
                ...config,
                outputs: { ...outputs, [name]: { to_state: event.target.value } },
              })}
            />
          </label>
        ))}
      </fieldset>
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

function SupervisorEditor({
  config,
  toolNames,
  skillNames,
  providerNames,
  agentOptions,
  targets,
  onChange,
}: {
  config: JsonObject;
  toolNames: string[];
  skillNames: string[];
  providerNames: string[];
  agentOptions: AgentOption[];
  targets: SupervisorTargetOption[];
  onChange: (config: JsonObject) => void;
}) {
  const inputs = asObject(config.inputs);
  const allowedTargets = asObject(config.allowed_targets);
  const knownTargetIds = new Set(targets.map((target) => target.id));
  const choices = [
    ...targets,
    ...Object.keys(allowedTargets)
      .filter((targetId) => !knownTargetIds.has(targetId))
      .map((targetId) => ({ id: targetId, label: targetId, eligible: false })),
  ];
  const finishOutput = asObject(config.finish_output);

  function replaceTarget(targetId: string, selected: boolean) {
    const next = { ...allowedTargets };
    if (selected) next[targetId] = { task_field: "task" };
    else delete next[targetId];
    onChange({ ...config, allowed_targets: next });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">The supervisor chooses one configured agent at a time. LangGraph returns every completed target here before the next decision.</p>
      <AgentSourceEditor
        config={config}
        toolNames={toolNames}
        skillNames={skillNames}
        providerNames={providerNames}
        agentOptions={agentOptions}
        requiresStructuredOutput
        onChange={onChange}
      />
      <StateInputEditor
        inputs={inputs}
        onChange={(nextInputs) => onChange({ ...config, inputs: nextInputs })}
      />
      <fieldset>
        <legend>Allowed agent targets</legend>
        <p className="field-help">These are dynamic routing relations, not React Flow edges. Only AGENT nodes without static edges can be selected.</p>
        <p className="field-help">Describe when to choose each target ID in the supervisor instructions above.</p>
        {choices.length === 0 && (
          <p className="field-help">Add an AGENT node and leave it detached from static edges.</p>
        )}
        {choices.map((target) => {
          const selected = target.id in allowedTargets;
          const targetConfig = asObject(allowedTargets[target.id]);
          return (
            <div className="mapping-row" key={target.id}>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={selected}
                  disabled={!target.eligible && !selected}
                  onChange={(event) => replaceTarget(target.id, event.target.checked)}
                />
                {target.label} <small>{target.id}</small>
              </label>
              {!target.eligible && <small className="field-help">Unavailable until its static edges are removed.</small>}
              {selected && (
                <label>
                  Delegated task field
                  <input
                    value={asString(targetConfig.task_field, "task")}
                    onChange={(event) => onChange({
                      ...config,
                      allowed_targets: {
                        ...allowedTargets,
                        [target.id]: { task_field: event.target.value },
                      },
                    })}
                  />
                </label>
              )}
            </div>
          );
        })}
      </fieldset>
      <label>
        Final result state path
        <input
          value={asString(finishOutput.to_state, "$.final")}
          onChange={(event) => onChange({
            ...config,
            finish_output: { to_state: event.target.value },
          })}
        />
      </label>
      <p className="field-help">The static OUTPUT node must expose this same state path.</p>
      <p className="field-help">The workflow ExecutionPolicy owns max_handoffs. The decision schema is fixed by the backend contract.</p>
    </div>
  );
}

function HumanApprovalEditor({ config, onChange }: {
  config: JsonObject;
  onChange: (config: JsonObject) => void;
}) {
  const output = asObject(config.output);
  return (
    <div className="typed-editor">
      <p className="field-help">
        Pause this workflow until an operator approves or rejects the request. The boolean response
        is written to shared state only after durable resume.
      </p>
      <label>
        Approval prompt
        <textarea
          value={asString(config.prompt)}
          placeholder="Publish this report?"
          onChange={(event) => onChange({ ...config, prompt: event.target.value })}
        />
      </label>
      <label>
        Response state path
        <input
          value={asString(output.to_state, "$.approved")}
          placeholder="$.approved"
          onChange={(event) => onChange({
            ...config,
            output: { to_state: event.target.value },
          })}
        />
      </label>
      <p className="field-help">The response schema is fixed to {`{ approved: boolean }`} in V2.5a.</p>
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
  supervisorTargets,
  workflowVersions,
  canDetach,
  onChange,
  onDetach,
  onDelete,
  onSaveAsAgent,
  inputSchema,
  outputSchema,
  onInputSchemaChange,
  onOutputSchemaChange,
}: NodeInspectorProps) {
  const config = node.data.config;

  return (
    <div>
      <p className="selection-kind">Node · {node.data.nodeType}</p>
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
      {node.data.nodeType === "supervisor" && (
        <SupervisorEditor
          config={config}
          toolNames={toolNames}
          skillNames={skillNames}
          providerNames={providerNames}
          agentOptions={agentOptions}
          targets={supervisorTargets}
          onChange={(next) => onChange({ config: next })}
        />
      )}
      {node.data.nodeType === "subworkflow" && (
        <SubworkflowEditor
          config={config}
          versions={workflowVersions}
          onChange={(next) => onChange({ config: next })}
        />
      )}
      {node.data.nodeType === "human_approval" && (
        <HumanApprovalEditor
          config={config}
          onChange={(next) => onChange({ config: next })}
        />
      )}
      {node.data.nodeType === "transform" && (
        <TransformEditor config={config} onChange={(next) => onChange({ config: next })} />
      )}
      {node.data.nodeType === "output" && (
        <WorkflowOutputEditor
          config={config}
          outputSchema={outputSchema}
          onChange={(next) => onChange({ config: next })}
          onOutputSchemaChange={onOutputSchemaChange}
        />
      )}
      {node.data.nodeType === "condition" && (
        <p className="field-help">Select an outgoing edge to configure its condition. One outgoing edge must remain the fallback.</p>
      )}
      {node.data.nodeType === "input" && (
        <WorkflowSchemaEditor
          title="Workflow inputs"
          help="Define the values a user, webhook, or scheduler must provide when starting this workflow."
          schema={inputSchema}
          onChange={onInputSchemaChange}
        />
      )}
      <details>
        <summary>Advanced node settings</summary>
        <label>Node ID<input value={node.id} readOnly /></label>
        {node.data.nodeType !== "input" && (
          <JsonEditor
            key={node.id}
            label="Configuration JSON"
            value={config}
            onApply={(value) => value && onChange({ config: value })}
          />
        )}
      </details>

      {!["input", "output"].includes(node.data.nodeType) && (
        <div className="node-actions">
          {node.data.nodeType === "agent" && asObject(config.source).type === "inline" && (
            <button type="button" onClick={onSaveAsAgent}>Save as reusable agent</button>
          )}
          <button type="button" disabled={!canDetach} onClick={onDetach}>Detach and reconnect</button>
          <button type="button" className="danger" onClick={onDelete}>Delete node</button>
        </div>
      )}
      {!canDetach && !["input", "output"].includes(node.data.nodeType) && (
        <small className="field-help">Detaching is available only for a simple node with one incoming and one outgoing edge.</small>
      )}
    </div>
  );
}
