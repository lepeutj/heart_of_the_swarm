import { useState } from "react";

import { exportAgentVersion, type AgentDetail } from "../../api";

export function AgentExportPanel({ version }: { version: AgentDetail | null }) {
  const [status, setStatus] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  async function download() {
    if (!version) return;
    setBusy(true);
    setFailed(false);
    setStatus("Building portable artifact…");
    try {
      const archive = await exportAgentVersion(version.version_id);
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${version.name.toLowerCase()}-v${version.version}-artifact.zip`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus(`Exported immutable version ${version.version}`);
    } catch (error) {
      setFailed(true);
      setStatus(error instanceof Error ? error.message : "Artifact export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="agent-export-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Export</p>
          <h3>Portable Docker artifact</h3>
        </div>
        <button type="button" disabled={!version || busy} onClick={download}>
          {busy ? "Exporting…" : "Export for Docker"}
        </button>
      </div>
      <p className="field-help">
        The archive contains the exact selected version. It contains declarations, never provider
        credentials or serialized LangChain objects.
      </p>
      <pre className="artifact-files">manifest.json{"\n"}agent.json</pre>
      {version && <small>Source version ID: {version.version_id}</small>}
      {status && <p className={failed ? "status error" : "status"}>{status}</p>}
    </section>
  );
}
