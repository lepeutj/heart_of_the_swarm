import { useEffect, useState } from "react";

import {
  createMCPServer,
  loadMCPServers,
  refreshMCPServer,
  testMCPServer,
  type MCPServer,
} from "../api";


export function useMCPServers(onCatalogueChanged: () => Promise<void>) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [message, setMessage] = useState("Loading MCP servers…");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const loaded = await loadMCPServers();
    setServers(loaded);
    setMessage(loaded.length ? "MCP sources loaded" : "No MCP server configured");
  }

  useEffect(() => {
    reload().catch((error: Error) => setMessage(error.message));
  }, []);

  async function run(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    try {
      await action();
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "MCP operation failed");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(name: string, url: string): Promise<boolean> {
    let server: MCPServer;
    setBusy(true);
    try {
      server = await createMCPServer(name, url);
      setServers((current) => [...current, server].sort((left, right) =>
        left.name.localeCompare(right.name)));
      setMessage(`Added ${server.name}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "MCP operation failed");
      setBusy(false);
      return false;
    }
    try {
      await onCatalogueChanged();
    } catch (error) {
      setMessage(
        `Added ${server.name}; catalogue reload failed: ${
          error instanceof Error ? error.message : "unknown error"
        }`,
      );
    } finally {
      setBusy(false);
    }
    return true;
  }

  return {
    servers,
    message,
    busy,
    add,
    test: (server: MCPServer) => run(async () => {
      const result = await testMCPServer(server.id);
      setMessage(
        result.reachable
          ? `${server.name}: ${result.tools.length} tool(s) discovered`
          : `${server.name}: ${result.error ?? "connection failed"}`,
      );
    }),
    refresh: (server: MCPServer) => run(async () => {
      const refreshed = await refreshMCPServer(server.id);
      await reload();
      await onCatalogueChanged();
      setMessage(
        refreshed.status.state === "ready"
          ? `Refreshed ${server.name}`
          : `${server.name}: ${refreshed.status.error ?? "refresh failed"}`,
      );
    }),
  };
}
