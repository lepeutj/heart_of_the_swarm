import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseEventStreamFrame,
  openWorkflowRunEventStream,
  request,
  respondToWorkflowInterruption,
  setAuthenticationToken,
} from "./api";

afterEach(() => {
  setAuthenticationToken(null);
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

  it("adds the bearer token without discarding caller headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    setAuthenticationToken("session-token");

    await request("/api/test", { headers: { "X-Request-Kind": "test" } });

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("X-Request-Kind")).toBe("test");
  });

  it("parses an SSE frame while ignoring transport formatting", () => {
    expect(parseEventStreamFrame([
      "id: 7",
      "event: workflow.event",
      'data: {"sequence":7}',
    ].join("\n"))).toEqual({
      id: "7",
      event: "workflow.event",
      data: '{"sequence":7}',
    });
    expect(parseEventStreamFrame(": keepalive")).toBeNull();
  });

  it("authenticates the workflow event stream and dispatches its durable event", async () => {
    const envelope = {
      event_id: "event-7",
      run_id: "run-1",
      thread_id: "thread-1",
      sequence: 7,
      event_type: "node.completed",
      data: { node_id: "research" },
      created_at: "2026-10-07T10:00:00Z",
    };
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          `id: 7\nevent: workflow.event\ndata: ${JSON.stringify(envelope)}\n\n`,
        ));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    setAuthenticationToken("session-token");

    const event = await new Promise<{ sequence: number; event_type: string }>((resolve) => {
      let stream: ReturnType<typeof openWorkflowRunEventStream>;
      stream = openWorkflowRunEventStream("run-1", (received) => {
        stream.close();
        resolve(received);
      }, () => undefined);
    });

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(event).toMatchObject({ sequence: 7, event_type: "node.completed" });
  });
});
