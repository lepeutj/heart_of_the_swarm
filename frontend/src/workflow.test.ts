import { describe, expect, it } from "vitest";
import {
  defaultConfig,
  normalizeLoadedNode,
  supervisorTargetOptions,
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
    expect(spec.schema_version).toBe("1");
  });

  it("uses schema V2 and serializes a bounded loop edge", () => {
    const nodes: EditorNode[] = [
      { id: "review", position: { x: 0, y: 0 }, data: { label: "Review", nodeType: "condition", config: {} } },
      { id: "draft", position: { x: 100, y: 0 }, data: { label: "Draft", nodeType: "agent", config: {} } },
    ];
    const edges: EditorEdge[] = [{
      id: "loop",
      source: "review",
      target: "draft",
      data: { loop: { id: "revision", max_iterations: 3 } },
    }];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Review loop",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: null,
        entrypoint: "draft",
      },
      nodes,
      edges,
    );

    expect(spec.schema_version).toBe("2");
    expect(spec.edges[0].loop).toEqual({ id: "revision", max_iterations: 3 });
  });

  it("uses schema V2 for an immutable subworkflow node", () => {
    const config = defaultConfig("subworkflow");
    const nodes: EditorNode[] = [{
      id: "research",
      position: { x: 0, y: 0 },
      data: { label: "Research", nodeType: "subworkflow", config },
    }];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Composed workflow",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: null,
        entrypoint: "research",
      },
      nodes,
      [],
    );

    expect(spec.schema_version).toBe("2");
    expect(config).toEqual({
      workflow_version_id: "",
      inputs: { request: { from_state: "$.request" } },
      outputs: { result: { to_state: "$.result" } },
    });
  });

  it("configures a typed human approval node in schema V2", () => {
    const config = defaultConfig("human_approval");
    const nodes: EditorNode[] = [{
      id: "approval",
      position: { x: 0, y: 0 },
      data: { label: "Approval", nodeType: "human_approval", config },
    }];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Approval workflow",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: null,
        entrypoint: "approval",
      },
      nodes,
      [],
    );

    expect(spec.schema_version).toBe("2");
    expect(config).toEqual({
      prompt: "Approve this workflow result?",
      output: { to_state: "$.approved" },
    });
  });

  it("uses schema V2 for fan-out and serializes parallel state reducers", () => {
    const nodes: EditorNode[] = [
      { id: "input", position: { x: 0, y: 0 }, data: { label: "Input", nodeType: "input", config: {} } },
      { id: "left", position: { x: 100, y: 0 }, data: { label: "Left", nodeType: "transform", config: {} } },
      { id: "right", position: { x: 100, y: 100 }, data: { label: "Right", nodeType: "transform", config: {} } },
    ];
    const edges: EditorEdge[] = [
      { id: "left", source: "input", target: "left", data: {} },
      { id: "right", source: "input", target: "right", data: {} },
    ];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Parallel",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: null,
        stateSchema: {
          "$.results": { schema: { type: "array" }, reducer: "append" },
        },
        entrypoint: "input",
      },
      nodes,
      edges,
    );

    expect(spec.schema_version).toBe("2");
    expect(spec.state_schema["$.results"].reducer).toBe("append");
  });

  it("configures an inline agent with the shared AgentSpec", () => {
    expect(defaultConfig("agent", "openrouter")).toMatchObject({
      source: {
        type: "inline",
        agent: {
          name: "NewAgent",
          model: { provider: "openrouter" },
          tools: [],
          skills: [],
        },
      },
      inputs: { request: { from_state: "$.request" } },
      outputs: { answer: { to_state: "$.answer" } },
    });
  });

  it("configures a typed supervisor with one existing agent target", () => {
    expect(defaultConfig("supervisor", "openrouter", "researcher")).toMatchObject({
      source: {
        type: "inline",
        agent: {
          name: "Supervisor",
          model: { provider: "openrouter" },
        },
      },
      inputs: { request: { from_state: "$.request" } },
      allowed_targets: { researcher: { task_field: "task" } },
      finish_output: { to_state: "$.final" },
      decision_schema: "supervisor_decision_v1",
      max_handoffs_ref: "execution_policy.max_handoffs",
    });
  });

  it("round-trips supervisor configuration without synthetic handoff edges", () => {
    const supervisorConfig = defaultConfig("supervisor", "openrouter", "researcher");
    const nodes: EditorNode[] = [
      { id: "input", position: { x: 0, y: 0 }, data: { label: "Input", nodeType: "input", config: {} } },
      {
        id: "supervisor",
        position: { x: 100, y: 0 },
        data: { label: "Supervisor", nodeType: "supervisor", config: supervisorConfig },
      },
      {
        id: "researcher",
        position: { x: 100, y: 100 },
        data: { label: "Researcher", nodeType: "agent", config: defaultConfig("agent") },
      },
      {
        id: "output",
        position: { x: 200, y: 0 },
        data: { label: "Output", nodeType: "output", config: { output_path: "$.final" } },
      },
    ];
    const edges: EditorEdge[] = [
      { id: "input-supervisor", source: "input", target: "supervisor", data: {} },
      { id: "supervisor-output", source: "supervisor", target: "output", data: {} },
    ];

    const spec = toWorkflowSpec(
      {
        id: "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
        name: "Supervised workflow",
        description: "Test",
        inputSchema: { type: "object" },
        outputSchema: null,
        entrypoint: "input",
      },
      nodes,
      edges,
    );
    const loaded = normalizeLoadedNode(spec.nodes.find((node) => node.id === "supervisor")!);

    expect(spec.schema_version).toBe("2");
    expect(spec.edges).toEqual([
      { source: "input", target: "supervisor" },
      { source: "supervisor", target: "output" },
    ]);
    expect(loaded).toEqual({ type: "supervisor", config: supervisorConfig });
  });

  it("offers only detached agents as eligible supervisor targets", () => {
    const nodes: EditorNode[] = [
      { id: "supervisor", position: { x: 0, y: 0 }, data: { label: "Supervisor", nodeType: "supervisor", config: {} } },
      { id: "researcher", position: { x: 0, y: 0 }, data: { label: "Researcher", nodeType: "agent", config: {} } },
      { id: "reviewer", position: { x: 0, y: 0 }, data: { label: "Reviewer", nodeType: "agent", config: {} } },
    ];
    const edges: EditorEdge[] = [
      { id: "connected", source: "researcher", target: "supervisor", data: {} },
    ];

    expect(supervisorTargetOptions(nodes, edges)).toEqual([
      { id: "researcher", label: "Researcher", eligible: false },
      { id: "reviewer", label: "Reviewer", eligible: true },
    ]);
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
