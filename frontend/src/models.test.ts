import { describe, expect, it } from "vitest";

import type { ModelDescriptor } from "./api";
import { filterModels, isFreeModel, modelLabel } from "./models";

const freeModel: ModelDescriptor = {
  provider: "openrouter",
  model_id: "vendor/free-model:free",
  name: "Free Model",
  context_length: 128_000,
  prompt_price: "0",
  completion_price: "0.000000",
  supports_tools: true,
  supports_structured_output: true,
};

const paidModel: ModelDescriptor = {
  ...freeModel,
  model_id: "vendor/paid-model",
  name: "Paid Model",
  prompt_price: "0.000001",
};

describe("model catalogue", () => {
  it("identifies models whose input and output prices are zero", () => {
    expect(isFreeModel(freeModel)).toBe(true);
    expect(isFreeModel(paidModel)).toBe(false);
  });

  it("filters by price and by model name or identifier", () => {
    expect(filterModels([freeModel, paidModel], "vendor/free", true)).toEqual([freeModel]);
    expect(filterModels([freeModel, paidModel], "Paid", false)).toEqual([paidModel]);
  });

  it("shows the runtime capabilities that matter during selection", () => {
    expect(modelLabel(freeModel)).toBe("Free Model · free · tools · structured");
  });
});
