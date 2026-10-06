import { afterEach, describe, expect, it, vi } from "vitest";

import { request, respondToWorkflowInterruption } from "./api";

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

  it.each([true, false])("submits an approval response and returns the continuation run", async (approved) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      run_id: "resumed-run",
      status: "queued",
    }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const accepted = await respondToWorkflowInterruption("interrupt-1", approved);

    expect(accepted.run_id).toBe("resumed-run");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workflow-interruptions/interrupt-1/response",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approved }),
      }),
    );
  });
});
