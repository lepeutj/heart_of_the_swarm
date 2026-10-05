import { afterEach, describe, expect, it, vi } from "vitest";

import { request } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API responses", () => {
  it("reports a non-JSON server error without attempting to parse an empty body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, {
      status: 500,
      statusText: "Internal Server Error",
    })));

    await expect(request("/api/test")).rejects.toThrow(
      "Request failed with HTTP 500 Internal Server Error",
    );
  });

  it("preserves structured API error details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { message: "Invalid workflow" } }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    )));

    await expect(request("/api/test")).rejects.toThrow("Invalid workflow");
  });
});
