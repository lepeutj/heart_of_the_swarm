import { describe, expect, it } from "vitest";
import { defaultConfig, toWorkflowSpec, type EditorEdge, type EditorNode } from "./workflow";

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

  it("configures a visual LLM with the shared AgentSpec", () => {
    expect(defaultConfig("llm")).toMatchObject({
      agent: { name: "NewAgent", tools: [] },
      inputs: { request: { from_state: "$.request" } },
      outputs: { answer: { to_state: "$.answer" } },
    });
  });

  it("configures a saved agent by immutable version ID", () => {
    expect(defaultConfig("agent")).toMatchObject({
      agent_version_id: "",
      inputs: { request: { from_state: "$.request" } },
      outputs: { answer: { to_state: "$.answer" } },
    });
  });

  it("configures a workflow output as a named state mapping", () => {
    expect(defaultConfig("output")).toEqual({
      outputs: { result: { from_state: "$.result" } },
    });
  });
});
