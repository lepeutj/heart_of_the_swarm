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

  it("provides an inline AgentSpec without redefining its fields", () => {
    expect(defaultConfig("agent")).toMatchObject({
      agent: { type: "inline", spec: { name: "NewAgent", tools: [] } },
      input_path: "$.request",
      output_path: "$.answer",
    });
  });
});
