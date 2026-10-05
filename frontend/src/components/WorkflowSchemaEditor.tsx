import { useState } from "react";

import type { JsonObject } from "../workflow";
import { JsonEditor } from "./JsonEditor";

const FIELD_TYPES = ["string", "number", "integer", "boolean", "array", "object"];

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function propertiesOf(schema: JsonObject): JsonObject {
  return asObject(schema.properties);
}

function requiredOf(schema: JsonObject): string[] {
  return Array.isArray(schema.required)
    ? schema.required.filter((name): name is string => typeof name === "string")
    : [];
}

function normalizedSchema(schema: JsonObject, properties: JsonObject, required: string[]): JsonObject {
  return {
    ...schema,
    type: "object",
    properties,
    required: required.filter((name) => name in properties),
    additionalProperties: false,
  };
}

export function WorkflowSchemaEditor({
  title,
  help,
  schema,
  onChange,
}: {
  title: string;
  help: string;
  schema: JsonObject;
  onChange: (schema: JsonObject) => void;
}) {
  const properties = propertiesOf(schema);
  const required = requiredOf(schema);
  const [draftNames, setDraftNames] = useState<Record<string, string>>({});

  function replaceProperties(next: JsonObject, nextRequired = required) {
    onChange(normalizedSchema(schema, next, nextRequired));
  }

  function rename(previous: string, next: string) {
    const normalized = next.trim();
    if (!normalized || (normalized !== previous && normalized in properties)) return;
    const nextProperties = Object.fromEntries(
      Object.entries(properties).map(([name, value]) => [name === previous ? normalized : name, value]),
    );
    replaceProperties(
      nextProperties,
      required.map((name) => name === previous ? normalized : name),
    );
    setDraftNames((current) => {
      const updated = { ...current };
      delete updated[previous];
      return updated;
    });
  }

  return (
    <div className="schema-editor">
      <h3>{title}</h3>
      <p className="field-help">{help}</p>
      {Object.entries(properties).map(([name, rawField]) => {
        const field = asObject(rawField);
        const type = typeof field.type === "string" ? field.type : "string";
        return (
          <div className="schema-field" key={name}>
            <label>
              Field name
              <input
                value={draftNames[name] ?? name}
                onChange={(event) => setDraftNames((current) => ({
                  ...current,
                  [name]: event.target.value,
                }))}
                onBlur={() => rename(name, draftNames[name] ?? name)}
              />
            </label>
            <label>
              Type
              <select
                value={type}
                onChange={(event) => replaceProperties({
                  ...properties,
                  [name]: {
                    ...field,
                    type: event.target.value,
                    ...(event.target.value === "array" ? { items: { type: "string" } } : {}),
                  },
                })}
              >
                {FIELD_TYPES.map((fieldType) => (
                  <option key={fieldType} value={fieldType}>{fieldType}</option>
                ))}
              </select>
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={required.includes(name)}
                onChange={(event) => replaceProperties(
                  properties,
                  event.target.checked
                    ? [...required, name]
                    : required.filter((requiredName) => requiredName !== name),
                )}
              />
              Required
            </label>
            <label>
              Hint
              <input
                value={typeof field.description === "string" ? field.description : ""}
                placeholder="Explain what this value represents"
                onChange={(event) => replaceProperties({
                  ...properties,
                  [name]: { ...field, description: event.target.value },
                })}
              />
            </label>
            <button
              type="button"
              onClick={() => {
                const next = { ...properties };
                delete next[name];
                replaceProperties(next, required.filter((requiredName) => requiredName !== name));
              }}
            >Remove field</button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => {
          let index = Object.keys(properties).length + 1;
          while (`field_${index}` in properties) index += 1;
          replaceProperties({
            ...properties,
            [`field_${index}`]: { type: "string", description: "" },
          });
        }}
      >Add field</button>
      <details>
        <summary>Advanced JSON Schema</summary>
        <JsonEditor label="Schema" value={schema} onApply={(value) => value && onChange(value)} />
      </details>
    </div>
  );
}
