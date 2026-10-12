import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addAgentVersion,
  createAgentVersionRun,
  designAgent,
  exportAgentVersion,
  loadAgentVersions,
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

  it("sends the Agent Studio design request through the existing builder contract", async () => {
    const spec = {
      name: "ResearchAgent",
      goal: "Research one topic",
      instructions: "Compare sources.",
      model: { provider: "openrouter", model_id: "model-a", temperature: 0 },
      tools: ["web_search"],
      skills: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      design_id: "design-1",
      trace_id: "trace-1",
      spec,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await designAgent("Research recent news", "openrouter", "model-a");

    expect(result.spec).toEqual(spec);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agents/design",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          task: "Research recent news",
          provider: "openrouter",
          model_id: "model-a",
        }),
      }),
    );
  });

  it("creates a new immutable version for the selected saved agent", async () => {
    const spec = {
      name: "ResearchAgent",
      goal: "Research one topic",
      instructions: "Compare sources and cite them.",
      model: { provider: "openrouter", model_id: "model-a", temperature: 0 },
      tools: ["web_search"],
      skills: ["research"],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: "agent-1",
      version_id: "version-2",
      version: 2,
      ...spec,
      spec,
    }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await addAgentVersion("agent-1", spec);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agents/agent-1/versions",
      expect.objectContaining({ method: "POST", body: JSON.stringify(spec) }),
    );
  });

  it("loads historical versions and queues the exact selected version", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([
        { id: "agent-1", version_id: "version-2", version: 2 },
        { id: "agent-1", version_id: "version-1", version: 1 },
      ]), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        run_id: "run-1",
        trace_id: "trace-1",
        agent_id: "agent-1",
        agent_version: 1,
        status: "queued",
      }), { status: 202, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const versions = await loadAgentVersions("agent-1");
    const run = await createAgentVersionRun("version-1", "Use historical behavior");

    expect(versions.map((version) => version.version_id)).toEqual(["version-2", "version-1"]);
    expect(run.agent_version).toBe(1);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/agent-versions/version-1/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: "Use historical behavior" }),
      }),
    );
  });

  it("downloads the exact selected agent artifact with bearer authentication", async () => {
    const archive = new Blob(["portable-agent"], { type: "application/zip" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(archive, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    setAuthenticationToken("session-token");

    const downloaded = await exportAgentVersion("version-3");

    expect(await downloaded.text()).toBe("portable-agent");
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agent-versions/version-3/artifact",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
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
