from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from heart_of_the_swarm.workflows.execution.reducers import reduce_parallel_values
from heart_of_the_swarm.workflows.spec import StateReducer, ValidatedWorkflowSpec
from heart_of_the_swarm.workflows.state import WorkflowState, get_path, set_path
from heart_of_the_swarm.workflows.validation.parallel import node_write_paths


@dataclass(frozen=True)
class ParallelNodeWrites:
    """Record the state paths one branch node may write when it executes."""

    node_id: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ParallelRuntimeBranch:
    """Describe one validated branch in the order used for deterministic merging."""

    id: str
    entry_node_id: str
    node_ids: tuple[str, ...]
    node_writes: tuple[ParallelNodeWrites, ...]

    @property
    def write_paths(self) -> tuple[str, ...]:
        """Return unique branch write paths in node declaration order."""
        return tuple(dict.fromkeys(path for item in self.node_writes for path in item.paths))

    def executed_write_paths(self, executed_nodes: tuple[str, ...]) -> set[str]:
        """Return only paths written by nodes that ran on the selected branch route."""
        executed = set(executed_nodes)
        return {
            path for item in self.node_writes if item.node_id in executed for path in item.paths
        }


@dataclass(frozen=True)
class ParallelStateReduction:
    """Describe how branch contributions to one exact state path are combined."""

    path: str
    reducer: StateReducer
    branch_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParallelRuntimeRegion:
    """Carry validated fan-out/fan-in metadata needed by the LangGraph adapter."""

    split_node_id: str
    branches: tuple[ParallelRuntimeBranch, ...]
    join_node_id: str
    reductions: tuple[ParallelStateReduction, ...]

    def branch_for_node(self, node_id: str) -> ParallelRuntimeBranch | None:
        """Return the branch containing a node, if that node is inside the region."""
        return next((branch for branch in self.branches if node_id in branch.node_ids), None)


def build_parallel_runtime_region(
    workflow: ValidatedWorkflowSpec,
) -> ParallelRuntimeRegion | None:
    """Project a validated region into immutable runtime metadata without graph analysis."""
    region = workflow.parallel_region
    if region is None:
        return None
    nodes = {node.id: node for node in workflow.nodes}
    branches: list[ParallelRuntimeBranch] = []
    path_branches: dict[str, list[str]] = {}
    for branch in region.branches:
        node_writes = tuple(
            ParallelNodeWrites(node_id=node_id, paths=node_write_paths(nodes[node_id]))
            for node_id in branch.node_ids
        )
        branches.append(
            ParallelRuntimeBranch(
                id=branch.id,
                entry_node_id=branch.entry_node_id,
                node_ids=branch.node_ids,
                node_writes=node_writes,
            )
        )
        write_paths = tuple(dict.fromkeys(path for item in node_writes for path in item.paths))
        for path in write_paths:
            path_branches.setdefault(path, []).append(branch.id)

    reductions = tuple(
        ParallelStateReduction(
            path=path,
            reducer=(
                workflow.state_schema[path].reducer
                if path in workflow.state_schema
                else StateReducer.REPLACE
            ),
            branch_ids=tuple(branch_ids),
        )
        for path, branch_ids in path_branches.items()
    )
    return ParallelRuntimeRegion(
        split_node_id=region.split_node_id,
        branches=tuple(branches),
        join_node_id=region.join_node_id,
        reductions=reductions,
    )


def merge_parallel_states(
    base_state: WorkflowState,
    region: ParallelRuntimeRegion,
    branch_states: dict[str, WorkflowState],
    executed_nodes: dict[str, tuple[str, ...]],
) -> WorkflowState:
    """Merge executed branch writes into a copied base state in declared branch order."""
    merged = deepcopy(base_state)
    branches = {branch.id: branch for branch in region.branches}
    for reduction in region.reductions:
        contributions: list[Any] = []
        for branch_id in reduction.branch_ids:
            branch = branches[branch_id]
            if reduction.path not in branch.executed_write_paths(executed_nodes[branch_id]):
                continue
            contributions.append(get_path(branch_states[branch_id], reduction.path))
        if contributions:
            set_path(
                merged,
                reduction.path,
                reduce_parallel_values(reduction.reducer, contributions),
            )
    return merged
