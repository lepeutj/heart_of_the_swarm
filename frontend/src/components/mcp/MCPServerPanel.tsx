import { useState } from "react";

import { useMCPServers } from "../../hooks/useMCPServers";


export function MCPServerPanel({
  onCatalogueChanged,
}: {
  onCatalogueChanged: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const mcp = useMCPServers(onCatalogueChanged);

  return (
    <section className="mcp-panel">
      <h2>MCP servers</h2>
      <label>
        Name
        <input
          value={name}
          placeholder="github"
          onChange={(event) => setName(event.target.value.toLowerCase())}
        />
      </label>
      <label>
        HTTP URL
        <input
          value={url}
          placeholder="https://example.com/mcp"
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <button
        type="button"
        disabled={mcp.busy || !name || !url}
        onClick={async () => {
          if (await mcp.add(name, url)) {
            setName("");
            setUrl("");
          }
        }}
      >
        Add server
      </button>
      <p className="field-help">{mcp.message}</p>

      <div className="mcp-server-list">
        {mcp.servers.map((server) => (
          <article key={server.id} className="mcp-server">
            <div>
              <strong>{server.name}</strong>
              <span className={`mcp-state ${server.status.state}`}>{server.status.state}</span>
            </div>
            <small>{server.url}</small>
            {server.status.error && <p className="field-error">{server.status.error}</p>}
            <ul>
              {server.status.tools.map((tool) => <li key={tool}>{tool}</li>)}
            </ul>
            <div className="mcp-actions">
              <button type="button" disabled={mcp.busy} onClick={() => mcp.test(server)}>
                Test
              </button>
              <button type="button" disabled={mcp.busy} onClick={() => mcp.refresh(server)}>
                Refresh
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
