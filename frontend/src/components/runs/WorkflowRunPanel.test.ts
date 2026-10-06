import { describe, expect, it } from "vitest";

import type { WorkflowRunEvent } from "../../api";
import { mergeRunEvents } from "./WorkflowRunPanel";

function event(id: string, runId: string, sequence: number): WorkflowRunEvent {
  return {
    id,
    workflow_run_id: runId,
    sequence,
    event_type: "node.completed",
    data: { node_id: id },
    created_at: "2026-10-06T00:00:00Z",
  };
}

describe("workflow run event history", () => {
  it("keeps prior attempt events while following a resumed run", () => {
    const interrupted = [event("input", "run-1", 1), event("approval", "run-1", 2)];
    const resumed = [event("approval-resumed", "run-2", 1), event("output", "run-2", 2)];

    expect(mergeRunEvents(interrupted, resumed).map((item) => item.id)).toEqual([
      "input",
      "approval",
      "approval-resumed",
      "output",
    ]);
    expect(mergeRunEvents(interrupted, interrupted)).toEqual(interrupted);
  });
});
