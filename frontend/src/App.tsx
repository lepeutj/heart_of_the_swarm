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
  loadCapabilities,
  loadProviderNames,
  loadToolNames,
  validateWorkflow,
  type ValidationIssue,
} from "./api";
import { EdgeInspector } from "./components/EdgeInspector";
import { JsonEditor } from "./components/JsonEditor";
import { NodeInspector } from "./components/NodeInspector";
import {
  detachNodeAndReconnect,
  findInsertionEdge,
  insertNodeOnEdge,
  moveEdgeAmongSiblings,
} from "./graph";
import {
  defaultConfig,
  toWorkflowSpec,
  type EditorEdge,
  type EditorNode,
  type JsonObject,
  type NodeType,
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
      config: { output_path: "$.request" },
    },
  },
];

const initialEdges: EditorEdge[] = [
  { id: "input-output", source: "input", target: "output", data: {} },
];

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState<EditorNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<EditorEdge>(initialEdges);
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof loadCapabilities>> | null>(null);
  const [toolNames, setToolNames] = useState<string[]>([]);
  const [providerNames, setProviderNames] = useState<string[]>([]);
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
  const [outputSchema, setOutputSchema] = useState<JsonObject>({ type: "string" });
  const workflowId = useRef(crypto.randomUUID());
  const sequence = useRef(1);

  useEffect(() => {
    Promise.all([loadCapabilities(), loadToolNames(), loadProviderNames()])
      .then(([catalogue, tools, providers]) => {
        setCapabilities(catalogue);
        setToolNames(tools);
        setProviderNames(providers);
        setStatus("Editor ready");
      })
      .catch((error: Error) => setStatus(error.message));
  }, []);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const issueNodeIds = useMemo(
    () => new Set(issues.flatMap((issue) => (issue.node_id ? [issue.node_id] : []))),
    [issues],
  );
  const displayNodes = useMemo(
    () => nodes.map((node) => ({
      ...node,
      className: issueNodeIds.has(node.id) ? "node-invalid" : undefined,
    })),
    [nodes, issueNodeIds],
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
  const spec = useMemo(
    () => toWorkflowSpec(
      {
        id: workflowId.current,
        name,
        description,
        inputSchema,
        outputSchema,
        entrypoint,
      },
      nodes,
      edges,
    ),
    [description, edges, entrypoint, inputSchema, name, nodes, outputSchema],
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
          config: defaultConfig(nodeType),
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

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Heart of the Swarm</p>
          <h1>Workflow editor</h1>
        </div>
        <div className="topbar-actions">
          <a href="/">Agent workspace</a>
          <button type="button" className="primary" onClick={validate}>Validate workflow</button>
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar palette">
          <h2>Workflow</h2>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <label>
            Entrypoint
            <select value={entrypoint} onChange={(event) => setEntrypoint(event.target.value)}>
              <option value="">Select a node</option>
              {nodes.map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}
            </select>
          </label>
          <JsonEditor label="Input schema" value={inputSchema} onApply={(value) => value && setInputSchema(value)} />
          <JsonEditor label="Output schema" value={outputSchema} onApply={(value) => value && setOutputSchema(value)} />

          <h2>Nodes</h2>
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
        </aside>

        <section className="canvas" aria-label="Workflow graph editor">
          <ReactFlow
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
            fitView
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
              providerNames={providerNames}
              canDetach={canDetachSelectedNode}
              onChange={updateSelectedNode}
              onDetach={detachSelectedNode}
              onDelete={deleteSelectedNode}
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
            <p className="muted">Select a node or edge to edit its declarative configuration.</p>
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
    </div>
  );
}
