import { describe, expect, it } from "vitest";

import { isTerminalAgentRun, totalUsage } from "./agentRun";

describe("Agent Studio run presentation", () => {
  it("keeps polling active states and stops for every terminal lifecycle state", () => {
    expect(isTerminalAgentRun("queued")).toBe(false);
    expect(isTerminalAgentRun("running")).toBe(false);
    expect(isTerminalAgentRun("cancel_requested")).toBe(false);
    expect(isTerminalAgentRun("completed")).toBe(true);
    expect(isTerminalAgentRun("failed")).toBe(true);
    expect(isTerminalAgentRun("cancelled")).toBe(true);
    expect(isTerminalAgentRun("timed_out")).toBe(true);
    expect(isTerminalAgentRun("interrupted")).toBe(true);
  });

  it("summarizes only the usage records attached to the displayed run", () => {
    expect(totalUsage([
      {
        provider: "openrouter", model_id: "model-a", stage: "agent", calls: 2,
        failures: 0, input_tokens: 120, output_tokens: 30, total_tokens: 150,
        cost: 0.01, average_latency_ms: 100,
      },
      {
        provider: "openrouter", model_id: "model-a", stage: "agent", calls: 1,
        failures: 1, input_tokens: 20, output_tokens: 0, total_tokens: 20,
        cost: 0.002, average_latency_ms: 50,
      },
    ])).toEqual({
      calls: 3,
      failures: 1,
      input_tokens: 140,
      output_tokens: 30,
      total_tokens: 170,
      cost: 0.012,
    });
  });
});
