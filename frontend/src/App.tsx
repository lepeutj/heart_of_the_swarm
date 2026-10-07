import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
} from "@xyflow/react";
import {
  createWorkflowVersion,
  createAgent,
  loadAgents,
  loadCapabilities,
  loadProviderNames,
  loadSkillNames,
  loadTools,
  loadLatestWorkflowVersion,
  loadWorkflow,
  loadWorkflows,
  saveWorkflow,
  setAuthenticationToken,
  uploadSkill,
  validateWorkflow,
  type AgentOption,
  type ValidationIssue,
  type WorkflowSummary,
  type AgentSpec,
  type ToolCapability,
  type WorkflowRunEvent,
  type WorkflowVersion,
} from "./api";
import { EdgeInspector } from "./components/EdgeInspector";
import { NodeInspector } from "./components/NodeInspector";
import { ParallelStateEditor } from "./components/ParallelStateEditor";
import { MCPServerPanel } from "./components/mcp/MCPServerPanel";
import { WorkflowRunPanel } from "./components/runs/WorkflowRunPanel";
import {
  detachNodeAndReconnect,
  findInsertionEdge,
  insertNodeOnEdge,
  moveEdgeAmongSiblings,
} from "./graph";
import {
  defaultConfig,
  normalizeLoadedNode,
  supervisorTargetOptions,
  toWorkflowSpec,
  type EditorEdge,
  type EditorNode,
  type JsonObject,
  type NodeType,
  type WorkflowEditorDocument,
  type WorkflowStateSchema,
} from "./workflow";

const initialNodes: EditorNode[] = [
  {
    id: "input",
    position: { x: 80, y: 160 },
    data: { label: "Input", nodeType: "input", config: {} },
  },
  {
    id: "output",
    position: { x: 520, y: 160 },
    data: {
      label: "Output",
      nodeType: "output",
      config: { outputs: { result: { from_state: "$.request" } } },
    },
  },
];

const initialEdges: EditorEdge[] = [
  { id: "input-output", source: "input", target: "output", data: {} },
];

const AUTH_TOKEN_KEY = "heart-of-the-swarm.auth-token";

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(AUTH_TOKEN_KEY) ?? "");
  const [draft, setDraft] = useState("");

  if (!token) {
    return (
      <main className="authentication-screen">
        <form
          className="authentication-card"
          onSubmit={(event) => {
            event.preventDefault();
            const nextToken = draft.trim();
            if (!nextToken) return;
            sessionStorage.setItem(AUTH_TOKEN_KEY, nextToken);
            setAuthenticationToken(nextToken);
            setToken(nextToken);
          }}
        >
          <p className="eyebrow">Heart of the Swarm</p>
          <h1>Authenticate</h1>
          <p className="field-help">
            Enter the development token or a JWT supplied by your identity provider.
          </p>
          <label>
            Bearer token
            <input
              type="password"
              autoComplete="off"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <button type="submit" className="primary">Open workflow editor</button>
        </form>
      </main>
    );
  }

  setAuthenticationToken(token);
  return (
    <WorkflowEditor
      onLogout={() => {
        sessionStorage.removeItem(AUTH_TOKEN_KEY);
        setAuthenticationToken(null);
        setToken("");
      }}
    />
  );
}

function WorkflowEditor({ onLogout }: { onLogout: () => void }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<EditorNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<EditorEdge>(initialEdges);
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof loadCapabilities>> | null>(null);
  const [tools, setTools] = useState<ToolCapability[]>([]);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [providerNames, setProviderNames] = useState<string[]>([]);
  const [agentOptions, setAgentOptions] = useState<AgentOption[]>([]);
  const [savedWorkflows, setSavedWorkflows] = useState<WorkflowSummary[]>([]);
  const [workflowVersions, setWorkflowVersions] = useState<WorkflowVersion[]>([]);
  const [publishedVersion, setPublishedVersion] = useState<WorkflowVersion | null>(null);
  const [runNodeStates, setRunNodeStates] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState<number | null>(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [graphKey, setGraphKey] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [insertionEdgeId, setInsertionEdgeId] = useState<string | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [status, setStatus] = useState("Loading backend capabilities…");
  const [name, setName] = useState("New workflow");
  const [description, setDescription] = useState("");
  const [entrypoint, setEntrypoint] = useState("input");
  const [inputSchema, setInputSchema] = useState<JsonObject>({
    type: "object",
    properties: { request: { type: "string" } },
    required: ["request"],
  });
  const [outputSchema, setOutputSchema] = useState<JsonObject | null>({
    type: "object",
    properties: { result: { type: "string" } },
    required: ["result"],
  });
  const [stateSchema, setStateSchema] = useState<WorkflowStateSchema>({});
  const [showRunPanel, setShowRunPanel] = useState(false);
  const workflowId = useRef<string>(crypto.randomUUID());
  const sequence = useRef(1);

  async function reloadTools() {
    setTools(await loadTools());
  }

  useEffect(() => {
    Promise.all([
      loadCapabilities(),
      loadTools(),
      loadSkillNames(),
      loadProviderNames(),
      loadAgents(),
      loadWorkflows(),
    ])
      .then(async ([catalogue, loadedTools, skills, providers, agents, workflows]) => {
        const versions = (await Promise.all(
          workflows
            .filter((workflow) => workflow.latest_version > 0)
            .map((workflow) => loadLatestWorkflowVersion(workflow.id)),
        )).filter((version): version is WorkflowVersion => version !== null);
        setCapabilities(catalogue);
        setTools(loadedTools);
        setSkillNames(skills);
        setProviderNames(providers);
        setAgentOptions(agents);
        setSavedWorkflows(workflows);
        setWorkflowVersions(versions);
        setStatus("Editor ready");
      })
      .catch((error: Error) => setStatus(error.message));
  }, []);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const toolNames = tools.map((tool) => tool.id);
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const issueNodeIds = useMemo(
    () => new Set(issues.flatMap((issue) => (issue.node_id ? [issue.node_id] : []))),
    [issues],
  );
  const displayNodes = useMemo(
    () => nodes.map((node) => ({
      ...node,
      className: [
        issueNodeIds.has(node.id) ? "node-invalid" : "",
        runNodeStates[node.id] ? `node-run-${runNodeStates[node.id]}` : "",
      ].filter(Boolean).join(" ") || undefined,
    })),
    [nodes, issueNodeIds, runNodeStates],
  );
  const displayEdges = useMemo(
    () => edges.map((edge) => ({
      ...edge,
      className: edge.id === insertionEdgeId ? "edge-insertion-target" : edge.className,
    })),
    [edges, insertionEdgeId],
  );
  const canDetachSelectedNode = useMemo(
    () => selectedNode
      ? detachNodeAndReconnect(nodes, edges, selectedNode.id, () => "preview") !== null
      : false,
    [edges, nodes, selectedNode],
  );
  const supervisorTargets = useMemo(
    () => supervisorTargetOptions(nodes, edges),
    [edges, nodes],
  );
  const spec = useMemo(
    () => toWorkflowSpec(
      {
        id: workflowId.current,
        name,
        description,
        inputSchema,
        outputSchema,
        stateSchema,
        entrypoint,
      },
      nodes,
      edges,
    ),
    [description, edges, entrypoint, inputSchema, name, nodes, outputSchema, stateSchema],
  );

  function addNode(nodeType: NodeType) {
    const id = `${nodeType}_${sequence.current++}`;
    setNodes((current) => [
      ...current,
      {
        id,
        position: { x: 220 + current.length * 36, y: 80 + current.length * 44 },
        data: {
          label: nodeType.charAt(0).toUpperCase() + nodeType.slice(1),
          nodeType,
          config: defaultConfig(
            nodeType,
            providerNames[0],
            supervisorTargets.find((target) => target.eligible)?.id,
          ),
        },
      },
    ]);
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
    setIssues([]);
  }

  function connect(connection: Connection) {
    setEdges((current) => addEdge(
      { ...connection, id: crypto.randomUUID(), data: {} },
      current,
    ));
    setIssues([]);
  }

  function updateSelectedNode(update: Partial<EditorNode["data"]>) {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => (
      node.id === selectedNodeId ? { ...node, data: { ...node.data, ...update } } : node
    )));
    setIssues([]);
  }

  function deleteSelectedNode() {
    if (!selectedNodeId) return;
    setNodes((current) => current.filter((node) => node.id !== selectedNodeId));
    setEdges((current) => current.filter(
      (edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId,
    ));
    if (entrypoint === selectedNodeId) setEntrypoint("");
    setSelectedNodeId(null);
    setIssues([]);
  }

  function detachSelectedNode() {
    if (!selectedNodeId) return;
    const result = detachNodeAndReconnect(nodes, edges, selectedNodeId);
    if (!result) return;
    setNodes(result.nodes);
    setEdges(result.edges);
    setSelectedNodeId(null);
    setIssues([]);
  }

  function updateInsertionTarget(dragged: EditorNode) {
    const currentNodes = nodes.map((node) => node.id === dragged.id ? dragged : node);
    const candidate = findInsertionEdge(dragged, currentNodes, edges);
    setInsertionEdgeId(candidate?.id ?? null);
    return candidate;
  }

  function finishNodeDrag(dragged: EditorNode) {
    const candidate = updateInsertionTarget(dragged);
    if (candidate) {
      setEdges((current) => insertNodeOnEdge(current, dragged.id, candidate.id));
      setIssues([]);
    }
    setInsertionEdgeId(null);
  }

  function updateSelectedEdge(update: Partial<EditorEdge>) {
    if (!selectedEdgeId) return;
    setEdges((current) => current.map((edge) => (
      edge.id === selectedEdgeId ? { ...edge, ...update } : edge
    )));
    setIssues([]);
  }

  function deleteSelectedEdge() {
    if (!selectedEdgeId) return;
    setEdges((current) => current.filter((edge) => edge.id !== selectedEdgeId));
    setSelectedEdgeId(null);
    setIssues([]);
  }

  function moveSelectedEdge(offset: number) {
    if (!selectedEdgeId) return;
    setEdges((current) => moveEdgeAmongSiblings(
      current,
      selectedEdgeId,
      offset < 0 ? -1 : 1,
    ));
    setIssues([]);
  }

  async function validate() {
    setStatus("Validating…");
    try {
      const result = await validateWorkflow(spec);
      setIssues(result.issues);
      setStatus(result.valid ? "Workflow is valid" : `${result.issues.length} validation issue(s)`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Validation failed");
    }
  }

  function editorDocument(): WorkflowEditorDocument {
    return {
      positions: Object.fromEntries(nodes.map((node) => [node.id, node.position])),
      viewport,
    };
  }

  async function saveDraft() {
    setStatus("Saving…");
    try {
      const saved = await saveWorkflow(spec, editorDocument(), revision);
      setRevision(saved.revision);
      setSavedWorkflows(await loadWorkflows());
      setStatus(`Draft saved · revision ${saved.revision}`);
      return saved;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Save failed");
      return null;
    }
  }

  async function publishVersion() {
    const saved = await saveDraft();
    if (!saved) return;
    try {
      const version = await createWorkflowVersion(saved.id);
      setPublishedVersion(version);
      setWorkflowVersions((current) => [
        ...current.filter((item) => item.workflow_id !== version.workflow_id),
        version,
      ]);
      setRunNodeStates({});
      setSavedWorkflows(await loadWorkflows());
      setStatus(`Workflow version ${version.version} created`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Version creation failed");
    }
  }

  async function saveSelectedInlineAgent() {
    if (!selectedNode || selectedNode.data.nodeType !== "agent") return;
    const source = selectedNode.data.config.source as JsonObject | undefined;
    if (source?.type !== "inline") return;
    const agent = source.agent as AgentSpec | undefined;
    if (!agent) return;
    setStatus("Saving agent…");
    try {
      const saved = await createAgent(agent);
      setAgentOptions(await loadAgents());
      updateSelectedNode({
        nodeType: "agent",
        config: {
          source: { type: "version", agent_version_id: saved.version_id },
          ...(selectedNode.data.config.input_path
            ? { input_path: selectedNode.data.config.input_path }
            : { inputs: selectedNode.data.config.inputs }),
          ...(selectedNode.data.config.output_path
            ? { output_path: selectedNode.data.config.output_path }
            : { outputs: selectedNode.data.config.outputs }),
          ...(selectedNode.data.config.response_schema
            ? { response_schema: selectedNode.data.config.response_schema }
            : {}),
        },
      });
      setStatus(`${saved.name} saved as agent version ${saved.version}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Agent save failed");
    }
  }

  async function openWorkflow(id: string) {
    if (!id) return;
    setStatus("Loading workflow…");
    try {
      const [saved, latestVersion] = await Promise.all([
        loadWorkflow(id),
        loadLatestWorkflowVersion(id),
      ]);
      workflowId.current = saved.id;
      setName(saved.spec.name);
      setDescription(saved.spec.description);
      setEntrypoint(saved.spec.entrypoint);
      setInputSchema(saved.spec.input_schema);
      setOutputSchema(saved.spec.output_schema);
      setStateSchema(saved.spec.state_schema ?? {});
      setNodes(saved.spec.nodes.map((node, index) => {
        const normalized = normalizeLoadedNode(node);
        return {
          id: node.id,
          position: saved.editor.positions[node.id] ?? { x: 120 + index * 180, y: 160 },
          data: {
            label: node.name,
            nodeType: normalized.type,
            config: normalized.config,
          },
        };
      }));
      setEdges(saved.spec.edges.map((edge) => ({
        id: crypto.randomUUID(),
        source: edge.source,
        target: edge.target,
        label: edge.label,
        data: { condition: edge.condition ?? null, loop: edge.loop ?? null },
      })));
      setRevision(saved.revision);
      setPublishedVersion(latestVersion);
      setRunNodeStates({});
      setViewport(saved.editor.viewport);
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      setIssues([]);
      sequence.current = saved.spec.nodes.length + 1;
      setGraphKey((current) => current + 1);
      setStatus(`Loaded revision ${saved.revision}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Load failed");
    }
  }

  function newWorkflow() {
    workflowId.current = crypto.randomUUID();
    setName("New workflow");
    setDescription("");
    setEntrypoint("input");
    setInputSchema({
      type: "object",
      properties: { request: { type: "string" } },
      required: ["request"],
    });
    setOutputSchema({
      type: "object",
      properties: { result: { type: "string" } },
      required: ["result"],
      additionalProperties: false,
    });
    setStateSchema({});
    setNodes(initialNodes);
    setEdges(initialEdges);
    setRevision(null);
    setPublishedVersion(null);
    setRunNodeStates({});
    setViewport({ x: 0, y: 0, zoom: 1 });
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setIssues([]);
    sequence.current = 1;
    setGraphKey((current) => current + 1);
    setStatus("New workflow draft");
  }

  function updateRunNodeStates(events: WorkflowRunEvent[]) {
    const states: Record<string, string> = {};
    for (const event of events) {
      const nodeId = event.data.node_id;
      if (typeof nodeId !== "string") continue;
      if (event.event_type === "node.started") states[nodeId] = "running";
      if (event.event_type === "node.completed") states[nodeId] = "completed";
      if (event.event_type === "node.failed") states[nodeId] = "failed";
    }
    setRunNodeStates(states);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Heart of the Swarm</p>
          <h1>Workflow editor</h1>
        </div>
        <div className="topbar-actions">
          <a href="/">Overview</a>
          <button type="button" onClick={onLogout}>Sign out</button>
          <button type="button" onClick={validate}>Validate</button>
          <button type="button" onClick={saveDraft}>Save draft</button>
          <button type="button" onClick={publishVersion}>Create version</button>
          <button type="button" className="primary" onClick={() => setShowRunPanel((open) => !open)}>
            {showRunPanel ? "Close run panel" : "Run workflow"}
          </button>
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar palette">
          <h2>Workflows</h2>
          <label>
            Saved workflows
            <select
              value={revision === null ? "" : workflowId.current}
              onChange={(event) => event.target.value
                ? openWorkflow(event.target.value)
                : newWorkflow()}
            >
              <option value="">New unsaved workflow</option>
              {savedWorkflows.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name} · r{workflow.revision} · v{workflow.latest_version}
                </option>
              ))}
            </select>
          </label>
          <h2>Nodes</h2>
          <p className="field-help">Add a node, then select it on the canvas to configure it.</p>
          <div className="node-palette">
            {capabilities?.nodes.map((capability) => (
              <button
                type="button"
                key={capability.type}
                disabled={!capability.available}
                title={capability.description}
                onClick={() => addNode(capability.type as NodeType)}
              >
                <span>{capability.type}</span>
                {!capability.available && <small>Later</small>}
              </button>
            ))}
          </div>

          <details className="resource-library">
            <summary>Skills and MCP resources</summary>
            <p className="field-help">Resources become available to agents and connectors in this builder.</p>
            <label>
              Upload Skill (.md)
              <input
                type="file"
                accept=".md,text/markdown"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  const skillName = file.name.replace(/\.md$/i, "");
                  try {
                    await uploadSkill(skillName, await file.text());
                    setSkillNames(await loadSkillNames());
                    setStatus(`Skill ${skillName} uploaded`);
                  } catch (error) {
                    setStatus(error instanceof Error ? error.message : "Skill upload failed");
                  }
                }}
              />
            </label>
            <MCPServerPanel onCatalogueChanged={reloadTools} />
          </details>
        </aside>

        <section className="canvas" aria-label="Workflow graph editor">
          <ReactFlow
            key={graphKey}
            nodes={displayNodes}
            edges={displayEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onNodeDrag={(_, node) => updateInsertionTarget(node)}
            onNodeDragStop={(_, node) => finishNodeDrag(node)}
            onNodeClick={(_, node) => {
              setSelectedNodeId(node.id);
              setSelectedEdgeId(null);
            }}
            onEdgeClick={(_, edge) => {
              setSelectedEdgeId(edge.id);
              setSelectedNodeId(null);
            }}
            onPaneClick={() => {
              setSelectedNodeId(null);
              setSelectedEdgeId(null);
            }}
            onMoveEnd={(_, nextViewport) => setViewport(nextViewport)}
            defaultViewport={viewport}
            fitView={revision === null}
            defaultEdgeOptions={{ interactionWidth: 48 }}
          >
            <Background gap={24} size={1} />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
        </section>

        <aside className="sidebar inspector">
          <h2>Inspector</h2>
          {selectedNode && (
            <NodeInspector
              node={selectedNode}
              toolNames={toolNames}
              capabilities={tools}
              skillNames={skillNames}
              providerNames={providerNames}
              agentOptions={agentOptions}
              supervisorTargets={supervisorTargets}
              workflowVersions={workflowVersions.filter(
                (version) => version.workflow_id !== workflowId.current,
              )}
              canDetach={canDetachSelectedNode}
              onChange={updateSelectedNode}
              onDetach={detachSelectedNode}
              onDelete={deleteSelectedNode}
              onSaveAsAgent={saveSelectedInlineAgent}
              inputSchema={inputSchema}
              outputSchema={outputSchema}
              onInputSchemaChange={setInputSchema}
              onOutputSchemaChange={setOutputSchema}
            />
          )}
          {selectedEdge && (
            <EdgeInspector
              edge={selectedEdge}
              sourceType={nodes.find((node) => node.id === selectedEdge.source)?.data.nodeType}
              routeIndex={edges.filter((edge) => edge.source === selectedEdge.source).findIndex((edge) => edge.id === selectedEdge.id)}
              routeCount={edges.filter((edge) => edge.source === selectedEdge.source).length}
              onChange={updateSelectedEdge}
              onMoveEarlier={() => moveSelectedEdge(-1)}
              onMoveLater={() => moveSelectedEdge(1)}
              onDelete={deleteSelectedEdge}
            />
          )}
          {!selectedNode && !selectedEdge && (
            <section className="workflow-overview">
              <p className="selection-kind">Workflow overview</p>
              <label>
                Name
                <input value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label>
                Description
                <textarea
                  value={description}
                  placeholder="Explain what this workflow accomplishes."
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <p className="field-help">
                Select INPUT to define the values received by the workflow. Select OUTPUT to choose the final results.
              </p>
              <dl className="workflow-facts">
                <div><dt>Nodes</dt><dd>{nodes.length}</dd></div>
                <div><dt>Entrypoint</dt><dd>{entrypoint || "Not set"}</dd></div>
                <div><dt>Draft</dt><dd>{revision === null ? "Unsaved" : `Revision ${revision}`}</dd></div>
                <div><dt>Published</dt><dd>{publishedVersion ? `Version ${publishedVersion.version}` : "No version"}</dd></div>
              </dl>
              <ParallelStateEditor value={stateSchema} onChange={setStateSchema} />
            </section>
          )}

          <div className="validation-summary">
            <h2>Validation</h2>
            <p className={issues.length ? "status error" : "status"}>{status}</p>
            {issues.map((issue, index) => (
              <article key={`${issue.code}-${index}`} className="issue">
                <strong>{issue.node_id ?? "Workflow"}</strong>
                <span>{issue.message}</span>
                <small>{issue.code}{issue.field ? ` · ${issue.field}` : ""}</small>
              </article>
            ))}
          </div>

          <details>
            <summary>WorkflowSpec JSON</summary>
            <pre>{JSON.stringify(spec, null, 2)}</pre>
          </details>
        </aside>
      </main>
      {showRunPanel && (
        <aside className="run-drawer">
          <WorkflowRunPanel
            version={publishedVersion}
            onEvents={updateRunNodeStates}
          />
        </aside>
      )}
    </div>
  );
}
