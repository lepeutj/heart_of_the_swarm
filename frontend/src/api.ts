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

let authenticationToken: string | null = null;

export function setAuthenticationToken(token: string | null): void {
  authenticationToken = token?.trim() || null;
}

function authenticatedHeaders(existing?: HeadersInit): Headers {
  const headers = new Headers(existing);
  if (authenticationToken) headers.set("Authorization", `Bearer ${authenticationToken}`);
  return headers;
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
  const headers = authenticatedHeaders(options?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...options,
    headers,
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
): WorkflowRunEventStream {
  const controller = new AbortController();
  let closed = false;
  let lastEventId: string | null = null;

  async function connect(): Promise<void> {
    while (!closed) {
      try {
        const headers = authenticatedHeaders({ Accept: "text/event-stream" });
        if (lastEventId) headers.set("Last-Event-ID", lastEventId);
        const response = await fetch(`/api/v1/workflow-runs/${runId}/stream`, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`SSE request failed: ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!closed) {
          const { done, value } = await reader.read();
          buffer = (buffer + decoder.decode(value, { stream: !done })).replaceAll("\r\n", "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const parsed = parseEventStreamFrame(frame);
            if (parsed?.event === "workflow.event") {
              if (parsed.id) lastEventId = parsed.id;
              onEvent(parseWorkflowRunStreamEvent(parsed.data));
            }
            boundary = buffer.indexOf("\n\n");
          }
          if (done) break;
        }
      } catch (error) {
        if (closed || (error instanceof DOMException && error.name === "AbortError")) return;
        onError();
      }
      if (!closed) await new Promise((resolve) => window.setTimeout(resolve, 1_000));
    }
  }

  void connect();
  return {
    close() {
      closed = true;
      controller.abort();
    },
  };
}

export interface WorkflowRunEventStream {
  close(): void;
}

export function parseEventStreamFrame(
  frame: string,
): { id: string | null; event: string; data: string } | null {
  if (!frame || frame.startsWith(":")) return null;
  let id: string | null = null;
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    const separator = line.indexOf(":");
    const field = separator >= 0 ? line.slice(0, separator) : line;
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, "") : "";
    if (field === "id") id = value;
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  return data.length ? { id, event, data: data.join("\n") } : null;
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
