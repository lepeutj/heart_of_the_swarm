import type { EditorEdge, EditorNode } from "./workflow";

const DEFAULT_NODE_WIDTH = 140;
const DEFAULT_NODE_HEIGHT = 44;
const INSERTION_DISTANCE = 56;

interface Point {
  x: number;
  y: number;
}

function center(node: EditorNode): Point {
  return {
    x: node.position.x + (node.measured?.width ?? DEFAULT_NODE_WIDTH) / 2,
    y: node.position.y + (node.measured?.height ?? DEFAULT_NODE_HEIGHT) / 2,
  };
}

function distanceToSegment(point: Point, start: Point, end: Point): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (dx === 0 && dy === 0) return Math.hypot(point.x - start.x, point.y - start.y);

  const projection = Math.max(
    0,
    Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)),
  );
  return Math.hypot(point.x - (start.x + projection * dx), point.y - (start.y + projection * dy));
}

export function findInsertionEdge(
  dragged: EditorNode,
  nodes: EditorNode[],
  edges: EditorEdge[],
): EditorEdge | null {
  if (["input", "output"].includes(dragged.data.nodeType)) return null;
  if (edges.some((edge) => edge.source === dragged.id || edge.target === dragged.id)) return null;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const point = center(dragged);
  let candidate: EditorEdge | null = null;
  let closest = INSERTION_DISTANCE;

  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    const distance = distanceToSegment(point, center(source), center(target));
    if (distance < closest) {
      closest = distance;
      candidate = edge;
    }
  }
  return candidate;
}

export function insertNodeOnEdge(
  edges: EditorEdge[],
  nodeId: string,
  edgeId: string,
  createId: () => string = () => crypto.randomUUID(),
): EditorEdge[] {
  const replaced = edges.find((edge) => edge.id === edgeId);
  if (!replaced || replaced.source === nodeId || replaced.target === nodeId) return edges;

  return edges.flatMap((edge) => edge.id === edgeId
    ? [
        {
          id: createId(),
          source: replaced.source,
          target: nodeId,
          data: replaced.data,
          label: replaced.label,
        },
        { id: createId(), source: nodeId, target: replaced.target, data: {} },
      ]
    : [edge]);
}

export function detachNodeAndReconnect(
  nodes: EditorNode[],
  edges: EditorEdge[],
  nodeId: string,
  createId: () => string = () => crypto.randomUUID(),
): { nodes: EditorNode[]; edges: EditorEdge[] } | null {
  const node = nodes.find((candidate) => candidate.id === nodeId);
  if (!node || ["input", "output", "condition"].includes(node.data.nodeType)) return null;

  const incoming = edges.filter((edge) => edge.target === nodeId);
  const outgoing = edges.filter((edge) => edge.source === nodeId);
  if (incoming.length !== 1 || outgoing.length !== 1) return null;

  const before = incoming[0];
  const after = outgoing[0];
  const duplicate = edges.some(
    (edge) => edge.source === before.source && edge.target === after.target,
  );
  const remaining = edges.flatMap((edge) => {
    if (edge.id === before.id) {
      return duplicate ? [] : [{
        id: createId(),
        source: before.source,
        target: after.target,
        data: before.data,
        label: before.label,
      }];
    }
    return edge.id === after.id ? [] : [edge];
  });

  return {
    nodes: nodes.filter((candidate) => candidate.id !== nodeId),
    edges: remaining,
  };
}

export function moveEdgeAmongSiblings(
  edges: EditorEdge[],
  edgeId: string,
  offset: -1 | 1,
): EditorEdge[] {
  const selected = edges.find((edge) => edge.id === edgeId);
  if (!selected) return edges;
  const siblingIndexes = edges
    .map((edge, index) => edge.source === selected.source ? index : -1)
    .filter((index) => index >= 0);
  const position = siblingIndexes.findIndex((index) => edges[index].id === edgeId);
  const targetPosition = position + offset;
  if (position < 0 || targetPosition < 0 || targetPosition >= siblingIndexes.length) return edges;

  const reordered = [...edges];
  const first = siblingIndexes[position];
  const second = siblingIndexes[targetPosition];
  [reordered[first], reordered[second]] = [reordered[second], reordered[first]];
  return reordered;
}
