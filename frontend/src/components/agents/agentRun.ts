import type { AgentRunStatus, UsageSummary } from "../../api";

const TERMINAL_STATUSES = new Set<AgentRunStatus>([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
  "interrupted",
]);

export function isTerminalAgentRun(status: AgentRunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function totalUsage(usage: UsageSummary[]): Pick<UsageSummary, "calls" | "failures" | "input_tokens" | "output_tokens" | "total_tokens" | "cost"> {
  return usage.reduce((total, item) => ({
    calls: total.calls + item.calls,
    failures: total.failures + item.failures,
    input_tokens: total.input_tokens + item.input_tokens,
    output_tokens: total.output_tokens + item.output_tokens,
    total_tokens: total.total_tokens + item.total_tokens,
    cost: total.cost + item.cost,
  }), { calls: 0, failures: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cost: 0 });
}
