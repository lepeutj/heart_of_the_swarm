import type { WorkflowEditorDocument, WorkflowSpec } from "./workflow";

export interface NodeCapability {
  type: string;
  available: boolean;
  description: string;
  config_schema: Record<string, unknown>;
}

export interface WorkflowCapabilities {
  schema_version: "2";
  workflow_schema: Record<string, unknown>;
  nodes: NodeCapability[];
}

export interface ValidationIssue {
  code: string;
  message: string;
  node_id: string | null;
  field: string | null;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

interface ProviderStatus {
  id: string;
  configured: boolean;
}

export interface ModelDescriptor {
  provider: string;
  model_id: string;
  name: string;
  context_length: number | null;
  prompt_price: string | null;
  completion_price: string | null;
  supports_tools: boolean;
  supports_structured_output: boolean;
}

export interface AgentOption {
  id: string;
  version_id: string;
  name: string;
  version: number;
}

export interface ToolCapability {
  id: string;
  source: "builtin" | "mcp";
  origin: string | null;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown> | null;
  annotations: Record<string, unknown>;
  schema_fingerprint: string;
}

export interface MCPServerStatus {
  name: string;
  state: "ready" | "error" | "not_loaded";
  tools: string[];
  error: string | null;
}

export interface MCPServer {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  status: MCPServerStatus;
}

export interface MCPServerTestResult {
  name: string;
  reachable: boolean;
  tools: string[];
  error: string | null;
}

export type AgentSpec = {
  name: string;
  goal: string;
  instructions: string;
  model: Record<string, unknown>;
  tools: string[];
  skills: string[];
};

export interface WorkflowSummary {
  id: string;
  name: string;
  description: string;
  revision: number;
  latest_version: number;
}

export interface WorkflowDraft extends WorkflowSummary {
  spec: WorkflowSpec;
  editor: WorkflowEditorDocument;
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  spec: WorkflowSpec;
}

export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "interrupted"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface WorkflowRun {
  run_id: string;
  trace_id: string;
  workflow_id: string;
  workflow_version_id: string;
  workflow_version: number;
  status: WorkflowRunStatus;
  input: Record<string, unknown>;
  output: unknown;
  error: string | null;
  attempt: number;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface WorkflowRunAccepted {
  run_id: string;
  trace_id: string;
  workflow_id: string;
  workflow_version_id: string;
  workflow_version: number;
  status: WorkflowRunStatus;
  thread_id?: string | null;
  attempt_index?: number;
  resumed_from_run_id?: string | null;
  resume_checkpoint_id?: string | null;
}

export type WorkflowInterruptionStatus = "pending" | "resolved" | "cancelled";

export interface WorkflowInterruption {
  id: string;
  workflow_version_id: string;
  thread_id: string;
  workflow_run_id: string;
  node_id: string;
  kind: "approval";
  prompt: string;
  response_schema: Record<string, unknown>;
  checkpoint_id: string;
  status: WorkflowInterruptionStatus;
  response: { approved: boolean } | null;
  created_at: string;
  resolved_at: string | null;
}

export interface WorkflowRunEvent {
  id: string;
  workflow_run_id: string;
  sequence: number;
  event_type: string;
  data: Record<string, unknown>;
  created_at: string;
}

interface WorkflowRunStreamEnvelope {
  event_id: string;
  run_id: string;
  thread_id: string | null;
  sequence: number;
  event_type: string;
  data: Record<string, unknown>;
  created_at: string;
}

function errorMessage(response: Response, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      const message = (detail as { message: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  const status = [response.status, response.statusText].filter(Boolean).join(" ");
  return `Request failed${status ? ` with HTTP ${status}` : ""}`;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    throw new Error(errorMessage(response, body));
  }
  return body as T;
}

export function loadCapabilities(): Promise<WorkflowCapabilities> {
  return request("/api/v1/workflows/capabilities");
}

export async function loadTools(): Promise<ToolCapability[]> {
  const result = await request<{ capabilities: ToolCapability[] }>("/api/v1/tools");
  return result.capabilities;
}

export function loadMCPServers(): Promise<MCPServer[]> {
  return request("/api/v1/mcp/servers");
}

export function createMCPServer(name: string, url: string): Promise<MCPServer> {
  return request("/api/v1/mcp/servers", {
    method: "POST",
    body: JSON.stringify({ name, url, enabled: true }),
  });
}

export function testMCPServer(id: string): Promise<MCPServerTestResult> {
  return request(`/api/v1/mcp/servers/${id}/test`, { method: "POST" });
}

export function refreshMCPServer(id: string): Promise<MCPServer> {
  return request(`/api/v1/mcp/servers/${id}/refresh`, { method: "POST" });
}

export async function loadSkillNames(): Promise<string[]> {
  const result = await request<{ skills: string[] }>("/api/v1/skills");
  return result.skills;
}

export function uploadSkill(name: string, content: string): Promise<{ name: string }> {
  return request("/api/v1/skills", {
    method: "POST",
    body: JSON.stringify({ name, content }),
  });
}

export async function loadProviderNames(): Promise<string[]> {
  const result = await request<ProviderStatus[]>("/api/v1/providers");
  return result.filter((provider) => provider.configured).map((provider) => provider.id);
}

export function loadModels(provider: string): Promise<ModelDescriptor[]> {
  return request(`/api/v1/models?provider=${encodeURIComponent(provider)}`);
}

export function validateWorkflow(spec: WorkflowSpec): Promise<ValidationResult> {
  return request("/api/v1/workflows/validate", {
    method: "POST",
    body: JSON.stringify(spec),
  });
}

export function loadAgents(): Promise<AgentOption[]> {
  return request("/api/v1/agents");
}

export function createAgent(spec: AgentSpec): Promise<AgentOption> {
  return request("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(spec),
  });
}

export function loadWorkflows(): Promise<WorkflowSummary[]> {
  return request("/api/v1/workflows");
}

export function loadWorkflow(id: string): Promise<WorkflowDraft> {
  return request(`/api/v1/workflows/${id}`);
}

export function saveWorkflow(
  spec: WorkflowSpec,
  editor: WorkflowEditorDocument,
  expectedRevision: number | null,
): Promise<WorkflowDraft> {
  return request(`/api/v1/workflows/${spec.id}`, {
    method: "PUT",
    body: JSON.stringify({
      spec,
      editor,
      expected_revision: expectedRevision,
    }),
  });
}

export function createWorkflowVersion(id: string): Promise<WorkflowVersion> {
  return request(`/api/v1/workflows/${id}/versions`, { method: "POST" });
}

export function loadLatestWorkflowVersion(id: string): Promise<WorkflowVersion | null> {
  return request(`/api/v1/workflows/${id}/versions/latest`);
}

export function createWorkflowRun(
  versionId: string,
  input: Record<string, unknown>,
): Promise<WorkflowRunAccepted> {
  return request(`/api/v1/workflow-versions/${versionId}/runs`, {
    method: "POST",
    body: JSON.stringify({ input }),
  });
}

export function loadWorkflowRun(runId: string): Promise<WorkflowRun> {
  return request(`/api/v1/workflow-runs/${runId}`);
}

export function parseWorkflowRunStreamEvent(data: string): WorkflowRunEvent {
  const envelope = JSON.parse(data) as WorkflowRunStreamEnvelope;
  return {
    id: envelope.event_id,
    workflow_run_id: envelope.run_id,
    sequence: envelope.sequence,
    event_type: envelope.event_type,
    data: envelope.data,
    created_at: envelope.created_at,
  };
}

export function openWorkflowRunEventStream(
  runId: string,
  onEvent: (event: WorkflowRunEvent) => void,
  onError: () => void,
): EventSource {
  const source = new EventSource(`/api/v1/workflow-runs/${runId}/stream`);
  source.addEventListener("workflow.event", (message) => {
    onEvent(parseWorkflowRunStreamEvent((message as MessageEvent<string>).data));
  });
  source.onerror = onError;
  return source;
}

export function loadWorkflowInterruptions(
  status: WorkflowInterruptionStatus = "pending",
): Promise<WorkflowInterruption[]> {
  return request(`/api/v1/workflow-interruptions?status=${encodeURIComponent(status)}`);
}

export function respondToWorkflowInterruption(
  interruptionId: string,
  approved: boolean,
): Promise<WorkflowRunAccepted> {
  return request(`/api/v1/workflow-interruptions/${interruptionId}/response`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}
