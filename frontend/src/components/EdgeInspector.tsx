import { useEffect, useState } from "react";
import type { EditorEdge, NodeType } from "../workflow";

const OPERATORS = [
  "equals",
  "not_equals",
  "exists",
  "not_exists",
  "contains",
  "greater_than",
  "less_than",
] as const;

interface EdgeInspectorProps {
  edge: EditorEdge;
  sourceType: NodeType | undefined;
  routeIndex?: number;
  routeCount?: number;
  onChange: (update: Partial<EditorEdge>) => void;
  onMoveEarlier?: () => void;
  onMoveLater?: () => void;
  onDelete: () => void;
}

function conditionObject(edge: EditorEdge): Record<string, unknown> | null {
  const value = edge.data?.condition;
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : null;
}

function loopObject(edge: EditorEdge): { id: string; max_iterations: number } | null {
  const value = edge.data?.loop;
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return {
    id: String(value.id ?? "revision"),
    max_iterations: Number(value.max_iterations ?? 3),
  };
}

function ConditionValue({ value, onApply }: { value: unknown; onApply: (value: unknown) => void }) {
  const [text, setText] = useState(JSON.stringify(value ?? ""));

  useEffect(() => setText(JSON.stringify(value ?? "")), [value]);

  return (
    <div className="inline-value-editor">
      <input value={text} onChange={(event) => setText(event.target.value)} />
      <button type="button" onClick={() => {
        try {
          onApply(JSON.parse(text));
        } catch {
          onApply(text);
        }
      }}>Apply</button>
    </div>
  );
}

export function EdgeInspector({
  edge,
  sourceType,
  routeIndex = 0,
  routeCount = 0,
  onChange,
  onMoveEarlier,
  onMoveLater,
  onDelete,
}: EdgeInspectorProps) {
  const condition = conditionObject(edge);
  const loop = loopObject(edge);
  const requiresValue = condition && !["exists", "not_exists"].includes(String(condition.operator));

  function setCondition(next: Record<string, unknown> | null) {
    onChange({ data: { ...edge.data, condition: next } });
  }

  function setLoop(next: { id: string; max_iterations: number } | null) {
    onChange({ data: { ...edge.data, loop: next } });
  }

  return (
    <div>
      <p className="selection-kind">Edge · {edge.source} → {edge.target}</p>
      <label>Label<input value={String(edge.label ?? "")} onChange={(event) => onChange({ label: event.target.value || undefined })} /></label>

      {sourceType === "condition" ? (
        <div className="typed-editor">
          <div className="route-order">
            <span>Route {routeIndex + 1} of {routeCount}</span>
            <button type="button" disabled={routeIndex === 0} onClick={onMoveEarlier}>Earlier</button>
            <button type="button" disabled={routeIndex >= routeCount - 1} onClick={onMoveLater}>Later</button>
          </div>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={condition === null}
              onChange={(event) => setCondition(event.target.checked ? null : {
                path: "$.result",
                operator: "equals",
                value: true,
              })}
            />
            Fallback route
          </label>
          {condition && (
            <>
              <label>State path<input value={String(condition.path ?? "")} onChange={(event) => setCondition({ ...condition, path: event.target.value })} /></label>
              <label>
                Operator
                <select value={String(condition.operator ?? "equals")} onChange={(event) => {
                  const operator = event.target.value;
                  const next: Record<string, unknown> = { ...condition, operator };
                  if (["exists", "not_exists"].includes(operator)) delete next.value;
                  else if (!("value" in next)) next.value = true;
                  setCondition(next);
                }}>
                  {OPERATORS.map((operator) => <option key={operator} value={operator}>{operator}</option>)}
                </select>
              </label>
              {requiresValue && (
                <label>Comparison value<ConditionValue value={condition.value} onApply={(value) => setCondition({ ...condition, value })} /></label>
              )}
            </>
          )}
          <p className="field-help">Conditional routes are evaluated in edge order. Exactly one outgoing edge must be the fallback.</p>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={loop !== null}
              onChange={(event) => setLoop(event.target.checked ? {
                id: "revision",
                max_iterations: 3,
              } : null)}
            />
            Bounded loop back edge
          </label>
          {loop && (
            <>
              <label>Loop ID<input value={loop.id} onChange={(event) => setLoop({ ...loop, id: event.target.value })} /></label>
              <label>Maximum iterations<input type="number" min="1" max="100" value={loop.max_iterations} onChange={(event) => setLoop({ ...loop, max_iterations: Number(event.target.value) })} /></label>
              <p className="field-help">This route may return to an earlier node. The runtime stops it after the declared number of traversals.</p>
            </>
          )}
        </div>
      ) : (
        <p className="field-help">This is an unconditional workflow transition.</p>
      )}

      <button type="button" className="danger" onClick={onDelete}>Delete edge</button>
    </div>
  );
}
