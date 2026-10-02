import type { WorkflowEditorDocument, WorkflowSpec } from "./workflow";

export interface NodeCapability {
  type: string;
  available: boolean;
  description: string;
  config_schema: Record<string, unknown>;
}

export interface WorkflowCapabilities {
  schema_version: "1";
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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail?.message ?? body.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

export function loadCapabilities(): Promise<WorkflowCapabilities> {
  return request("/api/v1/workflows/capabilities");
}

export async function loadTools(): Promise<ToolCapability[]> {
  const result = await request<{ capabilities: ToolCapability[] }>("/api/v1/tools");
  return result.capabilities;
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
  return result.map((provider) => provider.id);
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

export function createWorkflowVersion(id: string): Promise<{ version: number }> {
  return request(`/api/v1/workflows/${id}/versions`, { method: "POST" });
}
