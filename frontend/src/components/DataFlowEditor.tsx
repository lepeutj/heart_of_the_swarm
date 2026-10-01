import { useEffect, useState } from "react";

import type { JsonObject } from "../workflow";
import { JsonEditor } from "./JsonEditor";

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function namedBindings(
  config: JsonObject,
  field: "inputs" | "outputs",
  legacyField: "input_path" | "output_path",
  bindingField: "from_state" | "to_state",
): JsonObject {
  const configured = asObject(config[field]);
  if (Object.keys(configured).length > 0) return configured;
  const legacyPath = asString(config[legacyField]);
  return legacyPath ? { value: { [bindingField]: legacyPath } } : {};
}

function renameBinding(bindings: JsonObject, previous: string, nextName: string): JsonObject {
  if (!nextName || (nextName !== previous && nextName in bindings)) return bindings;
  return Object.fromEntries(
    Object.entries(bindings).map(([name, value]) => [name === previous ? nextName : name, value]),
  );
}

function without(config: JsonObject, field: string): JsonObject {
  const result = { ...config };
  delete result[field];
  return result;
}

function BindingRow({
  name,
  path,
  pathLabel,
  onRename,
  onPathChange,
  onRemove,
}: {
  name: string;
  path: string;
  pathLabel: string;
  onRename: (name: string) => void;
  onPathChange: (path: string) => void;
  onRemove: () => void;
}) {
  const [nameDraft, setNameDraft] = useState(name);
  useEffect(() => setNameDraft(name), [name]);
  return (
    <div className="mapping-row">
      <label>
        Field name
        <input
          value={nameDraft}
          onChange={(event) => setNameDraft(event.target.value)}
          onBlur={() => onRename(nameDraft)}
        />
      </label>
      <label>
        {pathLabel}
        <input value={path} onChange={(event) => onPathChange(event.target.value)} />
      </label>
      <button type="button" onClick={onRemove}>Remove field</button>
    </div>
  );
}

function responseSchemaFor(outputs: JsonObject, existing: unknown): JsonObject {
  const schema = asObject(existing);
  const properties = asObject(schema.properties);
  const names = Object.keys(outputs);
  return {
    title: asString(schema.title, "AgentNodeOutput"),
    description: asString(schema.description, "Structured fields returned by this agent node."),
    ...schema,
    type: "object",
    properties: Object.fromEntries(names.map((name) => [name, properties[name] ?? { type: "string" }])),
    required: names,
    additionalProperties: false,
  };
}

export function AgentDataFlowEditor({ config, onChange }: {
  config: JsonObject;
  onChange: (config: JsonObject) => void;
}) {
  const inputs = namedBindings(config, "inputs", "input_path", "from_state");
  const outputs = namedBindings(config, "outputs", "output_path", "to_state");

  function replaceInputs(nextInputs: JsonObject) {
    onChange({ ...without(config, "input_path"), inputs: nextInputs });
  }

  function replaceOutputs(nextOutputs: JsonObject) {
    const rest = without(config, "output_path");
    const needsSchema = Object.keys(nextOutputs).length > 1 || config.response_schema !== undefined;
    onChange({
      ...rest,
      outputs: nextOutputs,
      ...(needsSchema ? { response_schema: responseSchemaFor(nextOutputs, config.response_schema) } : {}),
    });
  }

  return (
    <>
      <fieldset>
        <legend>Inputs</legend>
        <p className="field-help">Named values read from the shared LangGraph state.</p>
        {Object.entries(inputs).map(([name, binding]) => {
          const value = asObject(binding);
          return (
            <BindingRow
              key={name}
              name={name}
              path={asString(value.from_state)}
              pathLabel="Source state path"
              onRename={(nextName) => replaceInputs(renameBinding(inputs, name, nextName))}
              onPathChange={(path) => replaceInputs({ ...inputs, [name]: { from_state: path } })}
              onRemove={() => {
                const next = { ...inputs };
                delete next[name];
                replaceInputs(next);
              }}
            />
          );
        })}
        <button type="button" onClick={() => {
          let index = Object.keys(inputs).length + 1;
          while (`input_${index}` in inputs) index += 1;
          replaceInputs({ ...inputs, [`input_${index}`]: { from_state: "$.request" } });
        }}>Add input</button>
      </fieldset>

      <fieldset>
        <legend>Outputs</legend>
        <p className="field-help">Map one text result or named structured fields into shared state.</p>
        {Object.entries(outputs).map(([name, binding]) => {
          const value = asObject(binding);
          return (
            <BindingRow
              key={name}
              name={name}
              path={asString(value.to_state)}
              pathLabel="Destination state path"
              onRename={(nextName) => replaceOutputs(renameBinding(outputs, name, nextName))}
              onPathChange={(path) => replaceOutputs({ ...outputs, [name]: { to_state: path } })}
              onRemove={() => {
                const next = { ...outputs };
                delete next[name];
                replaceOutputs(next);
              }}
            />
          );
        })}
        <button type="button" onClick={() => {
          let index = Object.keys(outputs).length + 1;
          while (`output_${index}` in outputs) index += 1;
          replaceOutputs({ ...outputs, [`output_${index}`]: { to_state: `$.output_${index}` } });
        }}>Add output</button>
      </fieldset>

      {config.response_schema !== undefined && (
        <JsonEditor
          label="Structured response schema"
          value={asObject(config.response_schema)}
          onApply={(value) => value && onChange({ ...config, response_schema: value })}
        />
      )}
      {config.response_schema === undefined && Object.keys(outputs).length === 1 && (
        <button
          type="button"
          onClick={() => onChange({ ...config, response_schema: responseSchemaFor(outputs, null) })}
        >Require structured output</button>
      )}
      {config.response_schema !== undefined && Object.keys(outputs).length === 1 && (
        <button
          type="button"
          onClick={() => onChange(without(config, "response_schema"))}
        >Use text output</button>
      )}
    </>
  );
}

export function WorkflowOutputEditor({ config, onChange }: {
  config: JsonObject;
  onChange: (config: JsonObject) => void;
}) {
  const outputs = namedBindings(config, "outputs", "output_path", "from_state");

  function replace(nextOutputs: JsonObject) {
    onChange({ ...without(config, "output_path"), outputs: nextOutputs });
  }

  return (
    <div className="typed-editor">
      <p className="field-help">Build the workflow response from named shared-state values.</p>
      {Object.entries(outputs).map(([name, binding]) => {
        const value = asObject(binding);
        return (
          <BindingRow
            key={name}
            name={name}
            path={asString(value.from_state)}
            pathLabel="Source state path"
            onRename={(nextName) => replace(renameBinding(outputs, name, nextName))}
            onPathChange={(path) => replace({ ...outputs, [name]: { from_state: path } })}
            onRemove={() => {
              const next = { ...outputs };
              delete next[name];
              replace(next);
            }}
          />
        );
      })}
      <button type="button" onClick={() => {
        let index = Object.keys(outputs).length + 1;
        while (`result_${index}` in outputs) index += 1;
        replace({ ...outputs, [`result_${index}`]: { from_state: `$.result_${index}` } });
      }}>Add workflow output</button>
    </div>
  );
}
