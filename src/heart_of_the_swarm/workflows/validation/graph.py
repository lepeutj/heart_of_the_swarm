from collections.abc import Iterable

from heart_of_the_swarm.workflows.spec import WorkflowEdge


def adjacency(node_ids: Iterable[str], edges: Iterable[WorkflowEdge]) -> dict[str, list[str]]:
    graph = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source in graph and edge.target in graph:
            graph[edge.source].append(edge.target)
    return graph


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visited: set[str] = set()
    active: set[str] = set()
    path: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        if node_id in active:
            start = path.index(node_id)
            return [*path[start:], node_id]
        if node_id in visited:
            return None
        visited.add(node_id)
        active.add(node_id)
        path.append(node_id)
        for target in graph[node_id]:
            cycle = visit(target)
            if cycle:
                return cycle
        path.pop()
        active.remove(node_id)
        return None

    for node_id in graph:
        cycle = visit(node_id)
        if cycle:
            return cycle
    return None


def reachable_nodes(graph: dict[str, list[str]], entrypoint: str) -> set[str]:
    if entrypoint not in graph:
        return set()
    reachable: set[str] = set()
    pending = [entrypoint]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(graph[node_id])
    return reachable


def nodes_reaching_outputs(graph: dict[str, list[str]], output_ids: Iterable[str]) -> set[str]:
    reverse = {node_id: [] for node_id in graph}
    for source, targets in graph.items():
        for target in targets:
            reverse[target].append(source)
    reaching: set[str] = set()
    pending = [node_id for node_id in output_ids if node_id in graph]
    while pending:
        node_id = pending.pop()
        if node_id in reaching:
            continue
        reaching.add(node_id)
        pending.extend(reverse[node_id])
    return reaching
