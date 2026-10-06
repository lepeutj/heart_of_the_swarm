import type { Edge, Node } from "@xyflow/react";

export type NodeType =
  | "input"
  | "agent"
  | "subworkflow"
  | "connector"
  | "condition"
  | "transform"
  | "output";

export type JsonObject = Record<string, unknown>;

export interface WorkflowNodeData extends Record<string, unknown> {
  label: string;
  nodeType: NodeType;
  config: JsonObject;
}

export type EditorNode = Node<WorkflowNodeData>;

export interface WorkflowEdgeData extends Record<string, unknown> {
  condition?: JsonObject | null;
  loop?: { id: string; max_iterations: number } | null;
}

export type EditorEdge = Edge<WorkflowEdgeData>;

export interface WorkflowSpec {
  schema_version: "1" | "2";
  id: string;
  name: string;
  description: string;
  input_schema: JsonObject;
  output_schema: JsonObject | null;
  nodes: Array<{
    id: string;
    type: NodeType;
    name: string;
    config: JsonObject;
  }>;
  edges: Array<{
    source: string;
    target: string;
    condition?: JsonObject;
    loop?: { id: string; max_iterations: number };
    label?: string;
  }>;
  entrypoint: string;
}

export interface WorkflowDocument {
  id: string;
  name: string;
  description: string;
  inputSchema: JsonObject;
  outputSchema: JsonObject | null;
  entrypoint: string;
}

export interface WorkflowEditorDocument {
  positions: Record<string, { x: number; y: number }>;
  viewport: { x: number; y: number; zoom: number };
}

export function normalizeLoadedNode(
  node: Omit<WorkflowSpec["nodes"][number], "type"> & { type: NodeType | "llm" },
): { type: NodeType; config: JsonObject } {
  if (node.type === "llm") {
    const { agent, ...config } = node.config;
    return {
      type: "agent",
      config: { ...config, source: { type: "inline", agent } },
    };
  }
  if (node.type === "agent" && !node.config.source && node.config.agent_version_id) {
    const { agent_version_id, ...config } = node.config;
    return {
      type: "agent",
      config: { ...config, source: { type: "version", agent_version_id } },
    };
  }
  return { type: node.type, config: node.config };
}

export function toWorkflowSpec(
  document: WorkflowDocument,
  nodes: EditorNode[],
  edges: EditorEdge[],
): WorkflowSpec {
  return {
    schema_version: (
      edges.some((edge) => edge.data?.loop)
      || nodes.some((node) => node.data.nodeType === "subworkflow")
    ) ? "2" : "1",
    id: document.id,
    name: document.name,
    description: document.description,
    input_schema: document.inputSchema,
    output_schema: document.outputSchema,
    entrypoint: document.entrypoint,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      name: node.data.label,
      config: node.data.config,
    })),
    edges: edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      ...(edge.data?.condition ? { condition: edge.data.condition } : {}),
      ...(edge.data?.loop ? { loop: edge.data.loop } : {}),
      ...(edge.label ? { label: String(edge.label) } : {}),
    })),
  };
}

export function defaultConfig(nodeType: NodeType, defaultProvider = "openai"): JsonObject {
  switch (nodeType) {
    case "agent":
      return {
        source: {
          type: "inline",
          agent: {
            name: "NewAgent",
            goal: "Complete the assigned task",
            instructions: "Return a clear and accurate answer.",
            model: { provider: defaultProvider, model_id: "", temperature: 0, max_tokens: null },
            tools: [],
            skills: [],
          },
        },
        inputs: { request: { from_state: "$.request" } },
        outputs: { answer: { to_state: "$.answer" } },
      };
    case "connector":
      return {
        capability_id: "http_get_json",
        inputs: { url: { from_state: "$.url" } },
        outputs: { result: { to_state: "$.result" } },
      };
    case "subworkflow":
      return {
        workflow_version_id: "",
        inputs: { request: { from_state: "$.request" } },
        outputs: { result: { to_state: "$.result" } },
      };
    case "transform":
      return { assign: { "$.result": { from_state: "$.request" } } };
    case "output":
      return { outputs: { result: { from_state: "$.result" } } };
    default:
      return {};
  }
}
