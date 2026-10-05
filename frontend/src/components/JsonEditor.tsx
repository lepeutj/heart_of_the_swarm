import { useEffect, useState } from "react";
import type { JsonObject } from "../workflow";

interface JsonEditorProps {
  label: string;
  value: JsonObject | null;
  allowNull?: boolean;
  onApply: (value: JsonObject | null) => void;
}

export function JsonEditor({ label, value, allowNull = false, onApply }: JsonEditorProps) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");

  useEffect(() => {
    setText(JSON.stringify(value, null, 2));
    setError("");
  }, [value]);

  function apply() {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed === null && allowNull) {
        onApply(null);
      } else if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        onApply(parsed as JsonObject);
      } else {
        throw new Error(allowNull ? "Expected an object or null." : "Expected an object.");
      }
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Invalid JSON.");
    }
  }

  return (
    <label className="json-editor">
      <span>{label}</span>
      <textarea value={text} onChange={(event) => setText(event.target.value)} spellCheck={false} />
      <button type="button" onClick={apply}>Apply JSON</button>
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}
