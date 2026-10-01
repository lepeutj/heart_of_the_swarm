import { describe, expect, it } from "vitest";
import {
  detachNodeAndReconnect,
  findInsertionEdge,
  insertNodeOnEdge,
  moveEdgeAmongSiblings,
} from "./graph";
import type { EditorEdge, EditorNode } from "./workflow";

function node(id: string, x: number, y: number, nodeType: EditorNode["data"]["nodeType"]): EditorNode {
  return { id, position: { x, y }, data: { label: id, nodeType, config: {} } };
}

describe("workflow graph editing", () => {
  it("finds and splits the edge under an unconnected node", () => {
    const nodes = [node("a", 0, 0, "input"), node("b", 400, 0, "output"), node("new", 200, 0, "agent")];
    const edges: EditorEdge[] = [{ id: "a-b", source: "a", target: "b", data: { condition: { path: "$.ok" } } }];

    expect(findInsertionEdge(nodes[2], nodes, edges)?.id).toBe("a-b");
    expect(insertNodeOnEdge(edges, "new", "a-b", (() => {
      let id = 0;
      return () => `edge-${++id}`;
    })())).toEqual([
      { id: "edge-1", source: "a", target: "new", data: edges[0].data, label: undefined },
      { id: "edge-2", source: "new", target: "b", data: {} },
    ]);
  });

  it("preserves conditional route priority when inserting a node", () => {
    const edges: EditorEdge[] = [
      { id: "first", source: "condition", target: "a", data: { condition: { path: "$.ok" } } },
      { id: "fallback", source: "condition", target: "b", data: { condition: null } },
    ];
    let sequence = 0;

    const updated = insertNodeOnEdge(edges, "new", "first", () => `edge-${++sequence}`);

    expect(updated.map(({ source, target }) => `${source}->${target}`)).toEqual([
      "condition->new",
      "new->a",
      "condition->b",
    ]);
    expect(updated[0].data).toEqual(edges[0].data);
  });

  it("detaches a simple node and reconnects its neighbors", () => {
    const nodes = [node("a", 0, 0, "input"), node("middle", 200, 0, "transform"), node("b", 400, 0, "output")];
    const edges: EditorEdge[] = [
      { id: "a-middle", source: "a", target: "middle", data: {} },
      { id: "middle-b", source: "middle", target: "b", data: {} },
    ];

    expect(detachNodeAndReconnect(nodes, edges, "middle", () => "a-b")).toEqual({
      nodes: [nodes[0], nodes[2]],
      edges: [{ id: "a-b", source: "a", target: "b", data: {}, label: undefined }],
    });
  });

  it("preserves sibling route priority when detaching a node", () => {
    const nodes = [
      node("condition", 0, 0, "condition"),
      node("middle", 200, 0, "transform"),
      node("a", 400, 0, "output"),
      node("b", 400, 200, "output"),
    ];
    const edges: EditorEdge[] = [
      { id: "first", source: "condition", target: "middle", data: { condition: { path: "$.ok" } } },
      { id: "middle-a", source: "middle", target: "a", data: {} },
      { id: "fallback", source: "condition", target: "b", data: { condition: null } },
    ];

    const updated = detachNodeAndReconnect(nodes, edges, "middle", () => "condition-a");

    expect(updated?.edges.map(({ source, target }) => `${source}->${target}`)).toEqual([
      "condition->a",
      "condition->b",
    ]);
    expect(updated?.edges[0].data).toEqual(edges[0].data);
  });

  it("does not infer how to detach a condition branch", () => {
    const nodes = [node("a", 0, 0, "input"), node("branch", 200, 0, "condition"), node("b", 400, 0, "output")];
    const edges: EditorEdge[] = [
      { id: "a-branch", source: "a", target: "branch", data: {} },
      { id: "branch-b", source: "branch", target: "b", data: {} },
    ];

    expect(detachNodeAndReconnect(nodes, edges, "branch")).toBeNull();
  });

  it("reorders only routes from the same condition", () => {
    const edges: EditorEdge[] = [
      { id: "first", source: "condition", target: "a" },
      { id: "other", source: "input", target: "condition" },
      { id: "second", source: "condition", target: "b" },
    ];

    expect(moveEdgeAmongSiblings(edges, "second", -1).map((edge) => edge.id)).toEqual([
      "second",
      "other",
      "first",
    ]);
  });
});
