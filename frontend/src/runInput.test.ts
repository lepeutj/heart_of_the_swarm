import { describe, expect, it } from "vitest";

import { buildRunInput } from "./runInput";

describe("workflow run input", () => {
  it("prefills required fields from the immutable workflow input schema", () => {
    expect(buildRunInput({
      type: "object",
      properties: {
        request: { type: "string" },
        limit: { type: "integer", default: 5 },
        optional: { type: "string" },
      },
      required: ["request", "limit"],
    })).toEqual({ request: "Enter your request", limit: 5 });
  });

  it("creates nested required objects without inventing optional fields", () => {
    expect(buildRunInput({
      type: "object",
      properties: {
        filters: {
          type: "object",
          properties: { enabled: { type: "boolean" } },
          required: ["enabled"],
        },
      },
      required: ["filters"],
    })).toEqual({ filters: { enabled: false } });
  });
});
