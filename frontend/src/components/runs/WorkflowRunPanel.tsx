import { useEffect, useRef, useState } from "react";

import {
  createWorkflowRun,
  loadWorkflowInterruptions,
  loadWorkflowRun,
  loadWorkflowRunEvents,
  respondToWorkflowInterruption,
  type WorkflowInterruption,
  type WorkflowRun,
  type WorkflowRunEvent,
  type WorkflowVersion,
} from "../../api";
import { buildRunInput } from "../../runInput";


const TERMINAL_STATUSES = new Set(["completed", "failed", "timed_out"]);

export function mergeRunEvents(
  current: WorkflowRunEvent[],
  received: WorkflowRunEvent[],
): WorkflowRunEvent[] {
  const known = new Set(current.map((event) => event.id));
  return [...current, ...received.filter((event) => !known.has(event.id))];
}


export function WorkflowRunPanel({
  version,
  onEvents,
}: {
  version: WorkflowVersion | null;
  onEvents: (events: WorkflowRunEvent[]) => void;
}) {
  const [input, setInput] = useState("{}");
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [events, setEvents] = useState<WorkflowRunEvent[]>([]);
  const [interruption, setInterruption] = useState<WorkflowInterruption | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [responding, setResponding] = useState(false);
  const [active, setActive] = useState(false);
  const [message, setMessage] = useState("Publish a workflow version before running it.");
  const onEventsRef = useRef(onEvents);
  const eventHistoryRef = useRef<WorkflowRunEvent[]>([]);
  onEventsRef.current = onEvents;

  useEffect(() => {
    setInput(JSON.stringify(buildRunInput(version?.spec.input_schema), null, 2));
    setRunId(null);
    setRun(null);
    setEvents([]);
    eventHistoryRef.current = [];
    setInterruption(null);
    setActive(false);
    onEventsRef.current([]);
    setMessage(
      version
        ? `Version ${version.version} is ready to run.`
        : "Publish a workflow version before running it.",
    );
  }, [version?.id]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const [nextRun, nextEvents] = await Promise.all([
          loadWorkflowRun(runId!),
          loadWorkflowRunEvents(runId!),
        ]);
        if (cancelled) return;
        setRun(nextRun);
        const combinedEvents = mergeRunEvents(eventHistoryRef.current, nextEvents);
        eventHistoryRef.current = combinedEvents;
        setEvents(combinedEvents);
        onEventsRef.current(combinedEvents);
        setMessage(`Run ${nextRun.status}`);
        if (nextRun.status === "interrupted") {
          const pending = await loadWorkflowInterruptions("pending");
          if (cancelled) return;
          const approval = pending.find((item) => item.workflow_run_id === nextRun.run_id) ?? null;
          setInterruption(approval);
          setMessage(approval ? "Workflow paused for approval" : "Workflow interrupted");
          setActive(false);
        } else if (TERMINAL_STATUSES.has(nextRun.status)) {
          setActive(false);
        } else {
          timer = setTimeout(poll, 750);
        }
      } catch (error) {
        if (!cancelled) {
          setMessage(error instanceof Error ? error.message : "Run polling failed");
          timer = setTimeout(poll, 1500);
        }
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  async function start() {
    if (!version) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(input);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Workflow input must be a JSON object");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Invalid JSON input");
      return;
    }

    setMessage("Queueing workflow…");
    setQueueing(true);
    setRun(null);
    setEvents([]);
    eventHistoryRef.current = [];
    setInterruption(null);
    onEventsRef.current([]);
    try {
      const accepted = await createWorkflowRun(
        version.id,
        parsed as Record<string, unknown>,
      );
      setRunId(accepted.run_id);
      setActive(true);
      setMessage("Run queued");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not queue workflow");
    } finally {
      setQueueing(false);
    }
  }

  async function answer(approved: boolean) {
    if (!interruption) return;
    setResponding(true);
    setMessage(approved ? "Approving workflow…" : "Rejecting workflow…");
    try {
      const accepted = await respondToWorkflowInterruption(interruption.id, approved);
      setInterruption(null);
      setRun(null);
      setRunId(accepted.run_id);
      setActive(true);
      setMessage("Response accepted · continuation queued");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not submit approval response");
    } finally {
      setResponding(false);
    }
  }

  return (
    <section className="workflow-run-panel">
      <h2>Run workflow</h2>
      <p className="field-help">
        {version ? `Immutable version ${version.version}` : "No published version selected"}
      </p>
      <label>
        Input JSON
        <textarea value={input} onChange={(event) => setInput(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={!version || queueing || active || interruption !== null}
        onClick={start}
      >
        Run
      </button>
      <p className={`status ${run?.status === "failed" || run?.status === "timed_out" ? "error" : ""}`}>
        {message}
      </p>
      {events.length > 0 && (
        <ol className="run-events">
          {events.map((event) => (
            <li key={event.id}>
              <strong>{event.event_type}</strong>
              {typeof event.data.node_id === "string" && <span>{event.data.node_id}</span>}
            </li>
          ))}
        </ol>
      )}
      {run?.error && <p className="field-error">{run.error}</p>}
      {interruption && (
        <section className="approval-request" aria-live="polite">
          <p className="selection-kind">Workflow paused</p>
          <h3>{interruption.prompt}</h3>
          <p className="field-help">Node · {interruption.node_id}</p>
          <div className="approval-actions">
            <button type="button" disabled={responding} onClick={() => answer(true)}>
              Approve
            </button>
            <button type="button" className="danger" disabled={responding} onClick={() => answer(false)}>
              Reject
            </button>
          </div>
        </section>
      )}
      {run?.status === "completed" && (
        <pre>{JSON.stringify(run.output, null, 2)}</pre>
      )}
    </section>
  );
}
