import type { WorkflowSpec } from "./workflow";

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

export async function loadToolNames(): Promise<string[]> {
  const result = await request<{ tools: string[] }>("/api/v1/tools");
  return result.tools;
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
