import { useEffect, useRef, useState } from "react";

import {
  createWorkflowRun,
  loadWorkflowRun,
  loadWorkflowRunEvents,
  type WorkflowRun,
  type WorkflowRunEvent,
  type WorkflowVersion,
} from "../../api";


const TERMINAL_STATUSES = new Set(["completed", "failed", "timed_out"]);


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
  const [queueing, setQueueing] = useState(false);
  const [active, setActive] = useState(false);
  const [message, setMessage] = useState("Publish a workflow version before running it.");
  const onEventsRef = useRef(onEvents);
  onEventsRef.current = onEvents;

  useEffect(() => {
    setRunId(null);
    setRun(null);
    setEvents([]);
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
        setEvents(nextEvents);
        onEventsRef.current(nextEvents);
        setMessage(`Run ${nextRun.status}`);
        if (TERMINAL_STATUSES.has(nextRun.status)) {
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
        disabled={!version || queueing || active}
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
      {run?.status === "completed" && (
        <pre>{JSON.stringify(run.output, null, 2)}</pre>
      )}
    </section>
  );
}
