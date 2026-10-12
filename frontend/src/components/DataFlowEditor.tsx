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
  fieldSchema,
  required,
  onFieldSchemaChange,
  onRequiredChange,
}: {
  name: string;
  path: string;
  pathLabel: string;
  onRename: (name: string) => void;
  onPathChange: (path: string) => void;
  onRemove: () => void;
  fieldSchema?: JsonObject;
  required?: boolean;
  onFieldSchemaChange?: (schema: JsonObject) => void;
  onRequiredChange?: (required: boolean) => void;
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
      {fieldSchema && onFieldSchemaChange && (
        <label>
          Result type
          <select
            value={asString(fieldSchema.type, "string")}
            onChange={(event) => onFieldSchemaChange({
              ...fieldSchema,
              type: event.target.value,
              ...(event.target.value === "array" ? { items: { type: "string" } } : {}),
            })}
          >
            {["string", "number", "integer", "boolean", "array", "object"].map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </select>
        </label>
      )}
      {fieldSchema && onRequiredChange && (
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={required ?? false}
            onChange={(event) => onRequiredChange(event.target.checked)}
          />
          Required in final response
        </label>
      )}
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

export function StateInputEditor({ inputs, onChange }: {
  inputs: JsonObject;
  onChange: (inputs: JsonObject) => void;
}) {
  return (
    <fieldset>
      <legend>Data received by this node</legend>
      <p className="field-help">Each field becomes context for the agent. Source paths refer to values produced earlier in the graph.</p>
      {Object.entries(inputs).map(([name, binding]) => {
        const value = asObject(binding);
        return (
          <BindingRow
            key={name}
            name={name}
            path={asString(value.from_state)}
            pathLabel="Source state path"
            onRename={(nextName) => onChange(renameBinding(inputs, name, nextName))}
            onPathChange={(path) => onChange({ ...inputs, [name]: { from_state: path } })}
            onRemove={() => {
              const next = { ...inputs };
              delete next[name];
              onChange(next);
            }}
          />
        );
      })}
      <button type="button" onClick={() => {
        let index = Object.keys(inputs).length + 1;
        while (`input_${index}` in inputs) index += 1;
        onChange({ ...inputs, [`input_${index}`]: { from_state: "$.request" } });
      }}>Add received value</button>
    </fieldset>
  );
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
    <details className="data-mapping">
      <summary>Data mapping</summary>
      <p className="field-help">Choose what this agent receives and where its response is stored. Most simple agents need only the default request and answer fields.</p>
      <StateInputEditor inputs={inputs} onChange={replaceInputs} />

      <fieldset>
        <legend>Response fields</legend>
        <p className="field-help">Store the answer so downstream nodes and the workflow output can reuse it.</p>
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
        }}>Add response field</button>
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
    </details>
  );
}

export function WorkflowOutputEditor({ config, outputSchema, onChange, onOutputSchemaChange }: {
  config: JsonObject;
  outputSchema: JsonObject | null;
  onChange: (config: JsonObject) => void;
  onOutputSchemaChange: (schema: JsonObject | null) => void;
}) {
  const outputs = namedBindings(config, "outputs", "output_path", "from_state");
  const schemaProperties = asObject(outputSchema?.properties);
  const required = Array.isArray(outputSchema?.required)
    ? outputSchema.required.filter((name): name is string => typeof name === "string")
    : [];

  function replace(nextOutputs: JsonObject) {
    onChange({ ...without(config, "output_path"), outputs: nextOutputs });
  }

  function replaceSchema(properties: JsonObject, requiredFields = required) {
    onOutputSchemaChange({
      ...outputSchema,
      type: "object",
      properties,
      required: requiredFields.filter((name) => name in properties),
      additionalProperties: false,
    });
  }

  function rename(previous: string, nextName: string) {
    if (!nextName || (nextName !== previous && nextName in outputs)) return;
    replace(renameBinding(outputs, previous, nextName));
    if (outputSchema) {
      replaceSchema(
        Object.fromEntries(Object.entries(schemaProperties).map(([name, value]) => [
          name === previous ? nextName : name,
          value,
        ])),
        required.map((name) => name === previous ? nextName : name),
      );
    }
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
            onRename={(nextName) => rename(name, nextName)}
            onPathChange={(path) => replace({ ...outputs, [name]: { from_state: path } })}
            fieldSchema={outputSchema ? asObject(schemaProperties[name] ?? { type: "string" }) : undefined}
            required={required.includes(name)}
            onFieldSchemaChange={outputSchema ? (fieldSchema) => replaceSchema({
              ...schemaProperties,
              [name]: fieldSchema,
            }) : undefined}
            onRequiredChange={outputSchema ? (isRequired) => replaceSchema(
              schemaProperties,
              isRequired ? [...required, name] : required.filter((field) => field !== name),
            ) : undefined}
            onRemove={() => {
              const next = { ...outputs };
              delete next[name];
              replace(next);
              if (outputSchema) {
                const nextProperties = { ...schemaProperties };
                delete nextProperties[name];
                replaceSchema(nextProperties, required.filter((field) => field !== name));
              }
            }}
          />
        );
      })}
      <button type="button" onClick={() => {
        let index = Object.keys(outputs).length + 1;
        while (`result_${index}` in outputs) index += 1;
        const name = `result_${index}`;
        replace({ ...outputs, [name]: { from_state: `$.${name}` } });
        if (outputSchema) replaceSchema({ ...schemaProperties, [name]: { type: "string" } });
      }}>Add workflow output</button>
      {outputSchema ? (
        <>
          <button type="button" onClick={() => onOutputSchemaChange(null)}>Allow untyped results</button>
          <details>
            <summary>Advanced result schema</summary>
            <JsonEditor
              label="Output JSON Schema"
              value={outputSchema}
              onApply={(value) => value && onOutputSchemaChange(value)}
            />
          </details>
        </>
      ) : (
        <button
          type="button"
          onClick={() => replaceSchema(Object.fromEntries(
            Object.keys(outputs).map((name) => [name, { type: "string" }]),
          ), Object.keys(outputs))}
        >Validate result types</button>
      )}
    </div>
  );
}
