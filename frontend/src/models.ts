import type { ModelDescriptor } from "./api";

export function isFreeModel(model: ModelDescriptor): boolean {
  return model.prompt_price !== null
    && model.completion_price !== null
    && Number(model.prompt_price) === 0
    && Number(model.completion_price) === 0;
}

export function filterModels(
  models: ModelDescriptor[],
  query: string,
  freeOnly: boolean,
): ModelDescriptor[] {
  const normalized = query.trim().toLowerCase();
  return models.filter((model) => (
    (!freeOnly || isFreeModel(model))
    && (!normalized
      || model.name.toLowerCase().includes(normalized)
      || model.model_id.toLowerCase().includes(normalized))
  ));
}

export function modelLabel(model: ModelDescriptor): string {
  const features = [
    isFreeModel(model) ? "free" : null,
    model.supports_tools ? "tools" : null,
    model.supports_structured_output ? "structured" : null,
  ].filter(Boolean);
  return `${model.name}${features.length ? ` · ${features.join(" · ")}` : ""}`;
}
