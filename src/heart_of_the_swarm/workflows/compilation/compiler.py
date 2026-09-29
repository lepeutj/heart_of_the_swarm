from collections import defaultdict
from copy import deepcopy
from heapq import heapify, heappop, heappush

from heart_of_the_swarm.workflows.compilation.errors import (
    WorkflowCompilationError,
    WorkflowCompilationIssue,
)
from heart_of_the_swarm.workflows.compilation.models import (
    CompiledConditionNode,
    CompiledInputNode,
    CompiledNode,
    CompiledOutputNode,
    CompiledRoute,
    CompiledTransformNode,
    ExecutionPlan,
)
from heart_of_the_swarm.workflows.configs import (
    ConditionNodeConfig,
    InputNodeConfig,
    OutputNodeConfig,
    TransformNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
)

_SUPPORTED_NODE_TYPES = {
    NodeType.INPUT,
    NodeType.TRANSFORM,
    NodeType.CONDITION,
    NodeType.OUTPUT,
}


class WorkflowCompiler:
    def compile(self, workflow: ValidatedWorkflowSpec) -> ExecutionPlan:
        if not isinstance(workflow, ValidatedWorkflowSpec):
            raise WorkflowCompilationError(
                [
                    WorkflowCompilationIssue(
                        code="workflow.compiler.unvalidated_input",
                        message="WorkflowCompiler requires a ValidatedWorkflowSpec.",
                    )
                ]
            )

        unsupported = [node for node in workflow.nodes if node.type not in _SUPPORTED_NODE_TYPES]
        if unsupported:
            raise WorkflowCompilationError(
                [
                    WorkflowCompilationIssue(
                        code="workflow.compiler.unsupported_node",
                        message=f"Node type '{node.type}' is not supported by this compiler.",
                        node_id=node.id,
                    )
                    for node in unsupported
                ]
            )

        dependencies, successors = _graph_metadata(workflow)
        execution_order = _stable_topological_order(workflow, dependencies, successors)
        outgoing = _outgoing_edges(workflow)
        nodes = tuple(
            _compile_node(node, dependencies[node.id], outgoing[node.id]) for node in workflow.nodes
        )
        return ExecutionPlan(
            schema_version=workflow.schema_version,
            workflow_id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            input_schema=deepcopy(workflow.input_schema),
            output_schema=deepcopy(workflow.output_schema),
            entrypoint=workflow.entrypoint,
            nodes=nodes,
            execution_order=execution_order,
        )


def _graph_metadata(
    workflow: ValidatedWorkflowSpec,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    dependencies: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    successors: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        if edge.source not in dependencies[edge.target]:
            dependencies[edge.target].append(edge.source)
        if edge.target not in successors[edge.source]:
            successors[edge.source].append(edge.target)
    return (
        {node_id: tuple(items) for node_id, items in dependencies.items()},
        {node_id: tuple(items) for node_id, items in successors.items()},
    )


def _stable_topological_order(
    workflow: ValidatedWorkflowSpec,
    dependencies: dict[str, tuple[str, ...]],
    successors: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    declared_index = {node.id: index for index, node in enumerate(workflow.nodes)}
    remaining = {node_id: len(items) for node_id, items in dependencies.items()}
    ready = [declared_index[node_id] for node_id, count in remaining.items() if count == 0]
    heapify(ready)
    ordered: list[str] = []
    while ready:
        node_id = workflow.nodes[heappop(ready)].id
        ordered.append(node_id)
        for target in successors[node_id]:
            remaining[target] -= 1
            if remaining[target] == 0:
                heappush(ready, declared_index[target])
    return tuple(ordered)


def _outgoing_edges(workflow: ValidatedWorkflowSpec) -> dict[str, list[WorkflowEdge]]:
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in workflow.edges:
        outgoing[edge.source].append(edge)
    return outgoing


def _compile_node(
    node: ValidatedWorkflowNode,
    dependencies: tuple[str, ...],
    edges: list[WorkflowEdge],
) -> CompiledNode:
    if node.type == NodeType.INPUT:
        assert isinstance(node.config, InputNodeConfig)
        return CompiledInputNode(
            id=node.id,
            name=node.name,
            dependencies=dependencies,
            config=node.config.model_copy(deep=True),
            next_node=edges[0].target,
        )
    if node.type == NodeType.TRANSFORM:
        assert isinstance(node.config, TransformNodeConfig)
        return CompiledTransformNode(
            id=node.id,
            name=node.name,
            dependencies=dependencies,
            config=node.config.model_copy(deep=True),
            next_node=edges[0].target,
        )
    if node.type == NodeType.CONDITION:
        assert isinstance(node.config, ConditionNodeConfig)
        routes = tuple(
            CompiledRoute(
                target=edge.target,
                condition=edge.condition.model_copy(deep=True),
                label=edge.label,
            )
            for edge in edges
            if edge.condition is not None
        )
        fallback = next(edge.target for edge in edges if edge.condition is None)
        return CompiledConditionNode(
            id=node.id,
            name=node.name,
            dependencies=dependencies,
            config=node.config.model_copy(deep=True),
            routes=routes,
            fallback=fallback,
        )
    assert node.type == NodeType.OUTPUT
    assert isinstance(node.config, OutputNodeConfig)
    return CompiledOutputNode(
        id=node.id,
        name=node.name,
        dependencies=dependencies,
        config=node.config.model_copy(deep=True),
    )
