import { useEffect, useState } from "react";

import { loadModels, type ModelDescriptor } from "../api";
import { filterModels, isFreeModel, modelLabel } from "../models";
import type { JsonObject } from "../workflow";

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function ModelEditor({
  model,
  providerNames,
  requiresTools,
  requiresStructuredOutput,
  onChange,
}: {
  model: JsonObject;
  providerNames: string[];
  requiresTools: boolean;
  requiresStructuredOutput: boolean;
  onChange: (model: JsonObject) => void;
}) {
  const provider = asString(model.provider);
  const modelId = asString(model.model_id);
  const [models, setModels] = useState<ModelDescriptor[]>([]);
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setModels([]);
    setError("");
    setFreeOnly(false);
    if (!provider) {
      setLoading(false);
      return () => { active = false; };
    }
    setLoading(true);
    loadModels(provider)
      .then((catalogue) => {
        if (active) setModels(catalogue);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load models");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [provider]);

  const filtered = filterModels(models, query, freeOnly);
  const selected = models.find((item) => item.model_id === modelId);
  const selectedVisible = filtered.some((item) => item.model_id === modelId);

  return (
    <>
      <label>
        Provider
        <select
          value={provider}
          onChange={(event) => onChange({
            ...model,
            provider: event.target.value,
            model_id: "",
          })}
        >
          {!providerNames.includes(provider) && (
            <option value={provider}>{provider || "No configured provider"}</option>
          )}
          {providerNames.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      {provider === "openrouter" && (
        <button
          type="button"
          onClick={() => onChange({ ...model, model_id: "openrouter/free" })}
        >
          Use OpenRouter free router
        </button>
      )}
      <label>
        Search models
        <input
          value={query}
          placeholder="Name or model ID"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {provider === "openrouter" && (
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={freeOnly}
            onChange={(event) => setFreeOnly(event.target.checked)}
          />
          Free models only
        </label>
      )}
      <label>
        Model
        <select
          value={modelId}
          disabled={loading}
          onChange={(event) => onChange({ ...model, model_id: event.target.value })}
        >
          <option value="">{loading ? "Loading models…" : "Select a model"}</option>
          {modelId && !selectedVisible && (
            <option value={modelId}>{selected ? modelLabel(selected) : `${modelId} · manual`}</option>
          )}
          {filtered.map((item) => (
            <option key={item.model_id} value={item.model_id}>{modelLabel(item)}</option>
          ))}
        </select>
      </label>
      <label>
        Manual model ID
        <input
          value={modelId}
          placeholder="apodex/apodex-1.1-mini:free"
          onChange={(event) => onChange({ ...model, model_id: event.target.value })}
        />
      </label>
      {error && <p className="field-error">{error}</p>}
      {selected && (
        <p className="field-help model-summary">
          {isFreeModel(selected) ? "Free" : "Paid"}
          {selected.context_length ? ` · ${selected.context_length.toLocaleString()} context` : ""}
          {` · ${selected.supports_tools ? "tools" : "no tools"}`}
          {` · ${selected.supports_structured_output ? "structured output" : "no structured output"}`}
        </p>
      )}
      {selected && requiresTools && !selected.supports_tools && (
        <p className="field-error">This model cannot use the selected tools.</p>
      )}
      {selected && requiresStructuredOutput && !selected.supports_structured_output && (
        <p className="field-error">This model does not advertise structured output support.</p>
      )}
      <div className="field-grid">
        <label>
          Temperature
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={typeof model.temperature === "number" ? model.temperature : 0}
            onChange={(event) => onChange({
              ...model,
              temperature: Number(event.target.value),
            })}
          />
        </label>
        <label>
          Max tokens
          <input
            type="number"
            min="1"
            value={typeof model.max_tokens === "number" ? model.max_tokens : ""}
            onChange={(event) => onChange({
              ...model,
              max_tokens: event.target.value ? Number(event.target.value) : null,
            })}
          />
        </label>
      </div>
    </>
  );
}
