from collections import defaultdict, deque

from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConnectorNodeConfig,
    HumanApprovalNodeConfig,
    SubworkflowNodeConfig,
    TransformNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import (
    ParallelBranch,
    ParallelRegion,
    StateReducer,
    ValidatedWorkflowNode,
    WorkflowEdge,
    WorkflowSpec,
)
from heart_of_the_swarm.workflows.validation.errors import WorkflowValidationIssue
from heart_of_the_swarm.workflows.validation.graph import (
    adjacency,
    nodes_reaching_outputs,
    reachable_nodes,
)


def analyze_parallel_region(
    spec: WorkflowSpec,
    nodes: list[ValidatedWorkflowNode],
) -> tuple[ParallelRegion | None, list[WorkflowValidationIssue]]:
    """Identify one simple fan-out/fan-in region and validate its concurrent writes."""
    by_id = {node.id: node for node in nodes}
    valid_edges = [
        edge
        for edge in spec.edges
        if edge.loop is None and edge.source in by_id and edge.target in by_id
    ]
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in valid_edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    split_ids = [
        node.id for node in nodes if node.type != NodeType.CONDITION and len(outgoing[node.id]) > 1
    ]
    if not split_ids:
        return None, []
    if spec.schema_version == "1":
        return None, [
            _issue(
                "workflow.parallel.requires_schema_v2",
                "Parallel fan-out requires workflow schema version 2.",
                split_ids[0],
                "schema_version",
            )
        ]
    if len(split_ids) > 1:
        return None, [
            _issue(
                "workflow.parallel.multiple_not_supported",
                "V2.3 supports one non-nested parallel region.",
                split_ids[1],
                "edges",
            )
        ]

    split_id = split_ids[0]
    targets = [edge.target for edge in outgoing[split_id]]
    graph = adjacency(by_id, valid_edges)
    join_id = _first_common_descendant(graph, targets, list(by_id))
    if join_id is None:
        return None, [
            _issue(
                "workflow.parallel.missing_join",
                f"Parallel branches from '{split_id}' require one identifiable all-join.",
                split_id,
                "edges",
            )
        ]

    reaching_join = nodes_reaching_outputs(graph, [join_id])
    branch_sets = [
        (reachable_nodes(graph, target) & reaching_join) - {join_id} for target in targets
    ]
    issues = _region_shape_errors(
        spec,
        split_id,
        join_id,
        targets,
        branch_sets,
        outgoing,
        incoming,
    )
    if issues:
        return None, issues

    node_order = {node.id: index for index, node in enumerate(nodes)}
    branches = tuple(
        ParallelBranch(
            id=f"{split_id}:{index}",
            entry_node_id=target,
            node_ids=tuple(sorted(branch, key=node_order.__getitem__)),
        )
        for index, (target, branch) in enumerate(zip(targets, branch_sets, strict=True))
    )
    region = ParallelRegion(
        split_node_id=split_id,
        branches=branches,
        join_node_id=join_id,
    )
    return region, _concurrent_write_errors(spec, region, by_id)


def node_write_paths(node: ValidatedWorkflowNode) -> tuple[str, ...]:
    """Return every business-state destination declared by one typed node."""
    config = node.config
    if isinstance(config, AgentNodeConfig):
        if config.output_path is not None:
            return (config.output_path,)
        return tuple(binding.to_state for binding in (config.outputs or {}).values())
    if isinstance(config, (ConnectorNodeConfig, SubworkflowNodeConfig)):
        return tuple(binding.to_state for binding in config.outputs.values())
    if isinstance(config, HumanApprovalNodeConfig):
        return (config.output.to_state,)
    if isinstance(config, TransformNodeConfig):
        return tuple(config.assign)
    return ()


def _first_common_descendant(
    graph: dict[str, list[str]],
    targets: list[str],
    node_order: list[str],
) -> str | None:
    distances = [_distances(graph, target) for target in targets]
    common = set.intersection(*(set(items) for items in distances))
    if not common:
        return None
    order = {node_id: index for index, node_id in enumerate(node_order)}
    return min(
        common,
        key=lambda node_id: (
            max(items[node_id] for items in distances),
            sum(items[node_id] for items in distances),
            order[node_id],
        ),
    )


def _distances(graph: dict[str, list[str]], start: str) -> dict[str, int]:
    distances = {start: 0}
    pending = deque([start])
    while pending:
        source = pending.popleft()
        for target in graph[source]:
            if target not in distances:
                distances[target] = distances[source] + 1
                pending.append(target)
    return distances


def _region_shape_errors(
    spec: WorkflowSpec,
    split_id: str,
    join_id: str,
    targets: list[str],
    branches: list[set[str]],
    outgoing: dict[str, list[WorkflowEdge]],
    incoming: dict[str, list[WorkflowEdge]],
) -> list[WorkflowValidationIssue]:
    """Reject ambiguous, empty, crossing, or looped parallel regions."""
    issues: list[WorkflowValidationIssue] = []
    for index, branch in enumerate(branches):
        if not branch:
            issues.append(
                _issue(
                    "workflow.parallel.empty_branch",
                    f"Parallel branch '{targets[index]}' contains no node before '{join_id}'.",
                    split_id,
                    "edges",
                )
            )
    for left in range(len(branches)):
        for right in range(left + 1, len(branches)):
            overlap = branches[left] & branches[right]
            if overlap:
                issues.append(
                    _issue(
                        "workflow.parallel.overlapping_branches",
                        "Parallel branches overlap before their join at nodes: "
                        + ", ".join(sorted(overlap)),
                        split_id,
                        "edges",
                    )
                )

    region_nodes = {split_id, join_id, *(node for branch in branches for node in branch)}
    branch_union = set().union(*branches)
    for branch in branches:
        for source in branch:
            unexpected = {
                edge.target
                for edge in outgoing[source]
                if edge.target not in branch and edge.target != join_id
            }
            if unexpected:
                issues.append(
                    _issue(
                        "workflow.parallel.branch_escape",
                        f"Parallel branch node '{source}' exits the region toward: "
                        + ", ".join(sorted(unexpected)),
                        source,
                        "edges",
                    )
                )
    external_join_writers = {
        edge.source for edge in incoming[join_id] if edge.source not in branch_union
    }
    if external_join_writers:
        issues.append(
            _issue(
                "workflow.parallel.join_has_external_predecessor",
                f"Parallel join '{join_id}' has predecessors outside its branches: "
                + ", ".join(sorted(external_join_writers)),
                join_id,
                "edges",
            )
        )
    if any(
        edge.loop is not None and (edge.source in region_nodes or edge.target in region_nodes)
        for edge in spec.edges
    ):
        issues.append(
            _issue(
                "workflow.parallel.loop_not_supported",
                "A bounded loop cannot enter, leave, or execute inside a parallel region.",
                split_id,
                "edges",
            )
        )
    return issues


def _concurrent_write_errors(
    spec: WorkflowSpec,
    region: ParallelRegion,
    nodes: dict[str, ValidatedWorkflowNode],
) -> list[WorkflowValidationIssue]:
    """Reject ambiguous writes across different branches with precise node context."""
    branch_writes: list[dict[str, set[str]]] = []
    for branch in region.branches:
        writes: dict[str, set[str]] = defaultdict(set)
        for node_id in branch.node_ids:
            for path in node_write_paths(nodes[node_id]):
                writes[path].add(node_id)
        branch_writes.append(writes)

    issues: list[WorkflowValidationIssue] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for left in range(len(branch_writes)):
        for right in range(left + 1, len(branch_writes)):
            for left_path, left_nodes in branch_writes[left].items():
                for right_path, right_nodes in branch_writes[right].items():
                    if not _paths_overlap(left_path, right_path):
                        continue
                    writers = tuple(sorted(left_nodes | right_nodes))
                    key = (left_path, right_path, writers)
                    if key in seen:
                        continue
                    seen.add(key)
                    declaration = spec.state_schema.get(left_path)
                    combinatory = (
                        left_path == right_path
                        and declaration is not None
                        and declaration.reducer in {StateReducer.APPEND, StateReducer.MERGE_DICT}
                    )
                    if combinatory:
                        continue
                    paths = (
                        f"'{left_path}'"
                        if left_path == right_path
                        else f"'{left_path}' and '{right_path}'"
                    )
                    issues.append(
                        _issue(
                            "workflow.parallel.write_conflict",
                            f"Concurrent writes to {paths} conflict between nodes: "
                            + ", ".join(writers),
                            region.split_node_id,
                            f"state_schema.{left_path}",
                        )
                    )
    return issues


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}.") or right.startswith(f"{left}.")


def _issue(
    code: str,
    message: str,
    node_id: str | None,
    field: str,
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id, field=field)
