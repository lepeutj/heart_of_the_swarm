import { useState } from "react";

import type { JsonObject, StateReducer, WorkflowStateSchema } from "../workflow";

const FIELD_TYPES = ["string", "number", "integer", "boolean", "array", "object"];

function schemaForReducer(schema: JsonObject, reducer: StateReducer): JsonObject {
  if (reducer === "append") return { ...schema, type: "array", items: schema.items ?? {} };
  if (reducer === "merge_dict") return { ...schema, type: "object" };
  return schema;
}

export function ParallelStateEditor({
  value,
  onChange,
}: {
  value: WorkflowStateSchema;
  onChange: (value: WorkflowStateSchema) => void;
}) {
  const [draftPaths, setDraftPaths] = useState<Record<string, string>>({});

  function replace(path: string, declaration: WorkflowStateSchema[string]) {
    onChange({ ...value, [path]: declaration });
  }

  function rename(previous: string, next: string) {
    const normalized = next.trim();
    if (!normalized || (normalized !== previous && normalized in value)) return;
    onChange(Object.fromEntries(
      Object.entries(value).map(([path, declaration]) => [
        path === previous ? normalized : path,
        declaration,
      ]),
    ));
    setDraftPaths((current) => {
      const updated = { ...current };
      delete updated[previous];
      return updated;
    });
  }

  return (
    <section className="schema-editor">
      <h3>Parallel state</h3>
      <p className="field-help">
        Declare only paths written by multiple parallel branches. Use append for lists and
        merge_dict for objects with distinct keys. Independent paths need no declaration.
      </p>
      {Object.entries(value).map(([path, declaration]) => {
        const type = typeof declaration.schema.type === "string"
          ? declaration.schema.type
          : "string";
        return (
          <div className="schema-field" key={path}>
            <label>
              State path
              <input
                value={draftPaths[path] ?? path}
                placeholder="$.results"
                onChange={(event) => setDraftPaths((current) => ({
                  ...current,
                  [path]: event.target.value,
                }))}
                onBlur={() => rename(path, draftPaths[path] ?? path)}
              />
            </label>
            <label>
              Reducer
              <select
                value={declaration.reducer}
                onChange={(event) => {
                  const reducer = event.target.value as StateReducer;
                  replace(path, {
                    reducer,
                    schema: schemaForReducer(declaration.schema, reducer),
                  });
                }}
              >
                <option value="replace">replace · one writer</option>
                <option value="append">append · combine lists</option>
                <option value="merge_dict">merge_dict · combine objects</option>
              </select>
            </label>
            <label>
              Value type
              <select
                value={type}
                disabled={declaration.reducer !== "replace"}
                onChange={(event) => replace(path, {
                  ...declaration,
                  schema: {
                    ...declaration.schema,
                    type: event.target.value,
                    ...(event.target.value === "array" ? { items: {} } : {}),
                  },
                })}
              >
                {FIELD_TYPES.map((fieldType) => (
                  <option key={fieldType} value={fieldType}>{fieldType}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => {
                const updated = { ...value };
                delete updated[path];
                onChange(updated);
              }}
            >Remove path</button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => {
          let index = Object.keys(value).length + 1;
          while (`$.results_${index}` in value) index += 1;
          onChange({
            ...value,
            [`$.results_${index}`]: {
              schema: { type: "array", items: {} },
              reducer: "append",
            },
          });
        }}
      >Add shared state path</button>
    </section>
  );
}
