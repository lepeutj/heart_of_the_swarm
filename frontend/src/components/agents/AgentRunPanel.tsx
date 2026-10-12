import { useEffect, useMemo, useState } from "react";

import {
  createAgentVersionRun,
  loadAgentRun,
  loadAgentRunEvents,
  loadAgentRunTrajectory,
  loadAgentRunUsage,
  type AgentDetail,
  type AgentRun,
  type AgentRunEvent,
  type TrajectoryStep,
  type UsageSummary,
} from "../../api";
import { isTerminalAgentRun, totalUsage } from "./agentRun";

interface AgentRunPanelProps {
  version: AgentDetail | null;
}

export function AgentRunPanel({ version }: AgentRunPanelProps) {
  const [task, setTask] = useState("");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [trajectory, setTrajectory] = useState<TrajectoryStep[]>([]);
  const [usage, setUsage] = useState<UsageSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setRun(null);
    setEvents([]);
    setTrajectory([]);
    setUsage([]);
    setError(null);
  }, [version?.version_id]);

  useEffect(() => {
    if (!run || isTerminalAgentRun(run.status)) return;
    let active = true;
    let timer: number | undefined;

    async function refresh() {
      try {
        const [nextRun, nextEvents] = await Promise.all([
          loadAgentRun(run!.run_id),
          loadAgentRunEvents(run!.run_id),
        ]);
        if (!active) return;
        if (isTerminalAgentRun(nextRun.status)) {
          const [nextTrajectory, nextUsage] = await Promise.all([
            loadAgentRunTrajectory(nextRun.run_id),
            loadAgentRunUsage(nextRun.run_id),
          ]);
          if (active) {
            setRun(nextRun);
            setEvents(nextEvents);
            setTrajectory(nextTrajectory);
            setUsage(nextUsage);
          }
          return;
        }
        setRun(nextRun);
        setEvents(nextEvents);
        timer = window.setTimeout(refresh, 750);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Could not follow run");
      }
    }

    void refresh();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [run?.run_id, run?.status]);

  const totals = useMemo(() => totalUsage(usage), [usage]);

  async function startRun() {
    if (!version || !task.trim()) return;
    setSubmitting(true);
    setError(null);
    setEvents([]);
    setTrajectory([]);
    setUsage([]);
    try {
      const accepted = await createAgentVersionRun(version.version_id, task.trim());
      setRun({
        ...accepted,
        input: task.trim(),
        output: null,
        error: null,
        attempt: 0,
        queued_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not queue run");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="agent-run-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Test & understand</p>
          <h3>Run immutable version</h3>
        </div>
        <span className={`run-status ${run?.status ?? "idle"}`}>{run?.status ?? "idle"}</span>
      </div>
      <p className="field-help">
        {version
          ? `This task will execute ${version.name} version ${version.version} (${version.version_id}).`
          : "Save or select an immutable version before running a task."}
      </p>
      <textarea
        value={task}
        placeholder="Ask this exact version to complete one task…"
        onChange={(event) => setTask(event.target.value)}
      />
      <button
        type="button"
        className="primary"
        disabled={!version || !task.trim() || submitting || Boolean(run && !isTerminalAgentRun(run.status))}
        onClick={startRun}
      >
        {submitting ? "Queueing…" : "Run exact version"}
      </button>
      {error && <p className="status error">{error}</p>}

      {run && (
        <div className="agent-run-details">
          <dl className="workflow-facts">
            <div><dt>Run</dt><dd>{run.run_id}</dd></div>
            <div><dt>Trace</dt><dd>{run.trace_id}</dd></div>
            <div><dt>Attempt</dt><dd>{run.attempt}</dd></div>
            <div><dt>Events</dt><dd>{events.length}</dd></div>
          </dl>

          {run.output && <article className="agent-run-result"><strong>Result</strong><p>{run.output}</p></article>}
          {run.error && (
            <article className="issue">
              <strong>Run failed</strong>
              <span>Execution stopped. Use trace {run.trace_id} to inspect the technical failure.</span>
            </article>
          )}

          <details open>
            <summary>Lifecycle events ({events.length})</summary>
            <ol className="run-events">
              {events.map((event) => <li key={event.id}><strong>{event.event_type}</strong>{new Date(event.created_at).toLocaleTimeString()}</li>)}
              {!events.length && <li>Waiting for the worker…</li>}
            </ol>
          </details>

          <details open={trajectory.length > 0}>
            <summary>Model and tool trajectory ({trajectory.length})</summary>
            {!isTerminalAgentRun(run.status) && (
              <p className="field-help">Trajectory steps are persisted when the current attempt ends.</p>
            )}
            <div className="trajectory-list">
              {trajectory.map((step) => (
                <article key={step.id}>
                  <strong>{step.sequence}. {step.event_type}</strong>
                  <span>{step.component ?? "agent"}</span>
                  <pre>{JSON.stringify(step.payload, null, 2)}</pre>
                </article>
              ))}
              {isTerminalAgentRun(run.status) && !trajectory.length && <p className="muted">No model or tool step was stored.</p>}
            </div>
          </details>

          <details open={usage.length > 0}>
            <summary>Usage</summary>
            <dl className="workflow-facts">
              <div><dt>Calls</dt><dd>{totals.calls}</dd></div>
              <div><dt>Tokens</dt><dd>{totals.total_tokens}</dd></div>
              <div><dt>Failures</dt><dd>{totals.failures}</dd></div>
              <div><dt>Cost</dt><dd>${totals.cost.toFixed(6)}</dd></div>
            </dl>
          </details>
        </div>
      )}
    </section>
  );
}
