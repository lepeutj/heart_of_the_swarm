import type { Edge, Node } from "@xyflow/react";

export type NodeType =
  | "input"
  | "agent"
  | "supervisor"
  | "subworkflow"
  | "connector"
  | "condition"
  | "transform"
  | "output";

export type JsonObject = Record<string, unknown>;
export type StateReducer = "replace" | "append" | "merge_dict";
export type WorkflowStateSchema = Record<
  string,
  { schema: JsonObject; reducer: StateReducer }
>;

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

export interface SupervisorTargetOption {
  id: string;
  label: string;
  eligible: boolean;
}

export interface WorkflowSpec {
  schema_version: "1" | "2";
  id: string;
  name: string;
  description: string;
  input_schema: JsonObject;
  output_schema: JsonObject | null;
  state_schema: WorkflowStateSchema;
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
  stateSchema?: WorkflowStateSchema;
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

export function supervisorTargetOptions(
  nodes: EditorNode[],
  edges: EditorEdge[],
): SupervisorTargetOption[] {
  const connected = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  return nodes
    .filter((node) => node.data.nodeType === "agent")
    .map((node) => ({
      id: node.id,
      label: node.data.label,
      eligible: !connected.has(node.id),
    }));
}

export function toWorkflowSpec(
  document: WorkflowDocument,
  nodes: EditorNode[],
  edges: EditorEdge[],
): WorkflowSpec {
  const outgoingCounts: Record<string, number> = {};
  for (const edge of edges) {
    outgoingCounts[edge.source] = (outgoingCounts[edge.source] ?? 0) + 1;
  }
  const nodeTypes = new Map(nodes.map((node) => [node.id, node.data.nodeType]));
  const hasParallelFanOut = Object.entries(outgoingCounts).some(
    ([source, count]) => count > 1 && nodeTypes.get(source) !== "condition",
  );
  const stateSchema = document.stateSchema ?? {};
  return {
    schema_version: (
      edges.some((edge) => edge.data?.loop)
      || nodes.some((node) => ["subworkflow", "supervisor"].includes(node.data.nodeType))
      || hasParallelFanOut
      || Object.keys(stateSchema).length > 0
    ) ? "2" : "1",
    id: document.id,
    name: document.name,
    description: document.description,
    input_schema: document.inputSchema,
    output_schema: document.outputSchema,
    state_schema: stateSchema,
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

export function defaultConfig(
  nodeType: NodeType,
  defaultProvider = "openai",
  supervisorTargetId?: string,
): JsonObject {
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
    case "supervisor":
      return {
        source: {
          type: "inline",
          agent: {
            name: "Supervisor",
            goal: "Coordinate the configured specialist agents",
            instructions: "Choose one configured target at a time, then finish with the final result.",
            model: { provider: defaultProvider, model_id: "", temperature: 0, max_tokens: null },
            tools: [],
            skills: [],
          },
        },
        inputs: { request: { from_state: "$.request" } },
        allowed_targets: supervisorTargetId ? { [supervisorTargetId]: { task_field: "task" } } : {},
        finish_output: { to_state: "$.final" },
        decision_schema: "supervisor_decision_v1",
        max_handoffs_ref: "execution_policy.max_handoffs",
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
