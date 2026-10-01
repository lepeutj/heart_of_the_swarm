import { describe, expect, it } from "vitest";
import {
  defaultConfig,
  normalizeLoadedNode,
  toWorkflowSpec,
  type EditorEdge,
  type EditorNode,
} from "./workflow";

describe("toWorkflowSpec", () => {
  it("serializes React Flow state into the backend contract", () => {
    const nodes: EditorNode[] = [
      { id: "input", position: { x: 0, y: 0 }, data: { label: "Input", nodeType: "input", config: {} } },
      {
        id: "output",
        position: { x: 100, y: 0 },
        data: { label: "Output", nodeType: "output", config: { output_path: "$.result" } },
      },
    ];
    const edges: EditorEdge[] = [
      { id: "edge", source: "input", target: "output", label: "done", data: {} },
    ];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Workflow",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: { type: "string" },
        entrypoint: "input",
      },
      nodes,
      edges,
    );

    expect(spec.nodes[1]).toEqual({
      id: "output",
      type: "output",
      name: "Output",
      config: { output_path: "$.result" },
    });
    expect(spec.edges[0]).toEqual({ source: "input", target: "output", label: "done" });
  });

  it("configures an inline agent with the shared AgentSpec", () => {
    expect(defaultConfig("agent")).toMatchObject({
      source: { type: "inline", agent: { name: "NewAgent", tools: [], skills: [] } },
      inputs: { request: { from_state: "$.request" } },
      outputs: { answer: { to_state: "$.answer" } },
    });
  });

  it("configures a deterministic connector", () => {
    expect(defaultConfig("connector")).toMatchObject({
      capability_id: "http_get_json",
      inputs: { url: { from_state: "$.url" } },
      outputs: { result: { to_state: "$.result" } },
    });
  });

  it("configures a workflow output as a named state mapping", () => {
    expect(defaultConfig("output")).toEqual({
      outputs: { result: { from_state: "$.result" } },
    });
  });

  it("normalizes a legacy LLM node into an inline agent", () => {
    const normalized = normalizeLoadedNode({
      id: "summarize",
      type: "llm",
      name: "Summarize",
      config: {
        agent: { name: "SummaryAgent" },
        input_path: "$.request",
        output_path: "$.answer",
      },
    });

    expect(normalized).toEqual({
      type: "agent",
      config: {
        source: { type: "inline", agent: { name: "SummaryAgent" } },
        input_path: "$.request",
        output_path: "$.answer",
      },
    });
  });

  it("normalizes a legacy saved agent reference", () => {
    const normalized = normalizeLoadedNode({
      id: "research",
      type: "agent",
      name: "Research",
      config: {
        agent_version_id: "f5427628-42a7-4698-9e8c-7489da8a7a41",
        input_path: "$.request",
        output_path: "$.answer",
      },
    });

    expect(normalized.config).toEqual({
      source: {
        type: "version",
        agent_version_id: "f5427628-42a7-4698-9e8c-7489da8a7a41",
      },
      input_path: "$.request",
      output_path: "$.answer",
    });
  });
});
