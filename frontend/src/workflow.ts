import type { Edge, Node } from "@xyflow/react";

export type NodeType =
  | "input"
  | "agent"
  | "llm"
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
}

export type EditorEdge = Edge<WorkflowEdgeData>;

export interface WorkflowSpec {
  schema_version: "1";
  id: string;
  name: string;
  description: string;
  input_schema: JsonObject;
  output_schema: JsonObject;
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
    label?: string;
  }>;
  entrypoint: string;
}

export interface WorkflowDocument {
  id: string;
  name: string;
  description: string;
  inputSchema: JsonObject;
  outputSchema: JsonObject;
  entrypoint: string;
}

export interface WorkflowEditorDocument {
  positions: Record<string, { x: number; y: number }>;
  viewport: { x: number; y: number; zoom: number };
}

export function toWorkflowSpec(
  document: WorkflowDocument,
  nodes: EditorNode[],
  edges: EditorEdge[],
): WorkflowSpec {
  return {
    schema_version: "1",
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
      ...(edge.label ? { label: String(edge.label) } : {}),
    })),
  };
}

export function defaultConfig(nodeType: NodeType): JsonObject {
  switch (nodeType) {
    case "agent":
      return {
        agent_version_id: "",
        inputs: { request: { from_state: "$.request" } },
        outputs: { answer: { to_state: "$.answer" } },
      };
    case "llm":
      return {
        agent: {
          name: "NewAgent",
          goal: "Complete the assigned task",
          instructions: "Return a clear and accurate answer.",
          model: { provider: "openai", model_id: "", temperature: 0, max_tokens: null },
          tools: [],
        },
        inputs: { request: { from_state: "$.request" } },
        outputs: { answer: { to_state: "$.answer" } },
      };
    case "transform":
      return { assign: { "$.result": { from_state: "$.request" } } };
    case "output":
      return { outputs: { result: { from_state: "$.result" } } };
    default:
      return {};
  }
}
