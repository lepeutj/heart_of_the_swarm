from collections import defaultdict

from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    OutputNodeConfig,
    SupervisorNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import (
    ParallelRegion,
    ValidatedWorkflowNode,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from heart_of_the_swarm.workflows.supervisors import SUPERVISOR_DECISION_SCHEMA
from heart_of_the_swarm.workflows.validation.errors import WorkflowValidationIssue


def supervisor_reachability_edges(
    nodes: list[ValidatedWorkflowNode],
    node_ids: set[str],
) -> list[WorkflowEdge]:
    """Project declared supervisor targets into validation-only reachability edges."""
    edges: list[WorkflowEdge] = []
    for node in nodes:
        if not isinstance(node.config, SupervisorNodeConfig):
            continue
        for target in node.config.allowed_targets:
            if target not in node_ids or target == node.id:
                continue
            edges.extend(
                [
                    WorkflowEdge(source=node.id, target=target),
                    WorkflowEdge(source=target, target=node.id),
                ]
            )
    return edges


def validate_supervisors(
    spec: WorkflowSpec,
    nodes: list[ValidatedWorkflowNode],
) -> list[WorkflowValidationIssue]:
    """Validate V2.4a supervisor references without compiling runtime routing."""
    issues: list[WorkflowValidationIssue] = []
    by_id = {node.id: node for node in nodes}
    declared_by_id = {node.id: node for node in spec.nodes}
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in spec.edges:
        incoming[edge.target].append(edge)
        outgoing[edge.source].append(edge)

    supervisors = [node for node in nodes if isinstance(node.config, SupervisorNodeConfig)]
    if len(supervisors) > 1:
        issues.append(
            _issue(
                "workflow.supervisor.multiple_not_supported",
                "V2.4a supports exactly one supervisor node per workflow.",
                supervisors[1].id,
                "nodes",
            )
        )

    for supervisor in supervisors:
        config = supervisor.config
        if not isinstance(config, SupervisorNodeConfig):
            continue
        if spec.schema_version != "2":
            issues.append(
                _issue(
                    "workflow.supervisor.requires_schema_v2",
                    "Supervisor nodes require workflow schema version 2.",
                    supervisor.id,
                    "schema_version",
                )
            )
        if config.decision_schema != SUPERVISOR_DECISION_SCHEMA:
            issues.append(
                _issue(
                    "workflow.supervisor.invalid_decision_contract",
                    f"Supervisor decision_schema must be '{SUPERVISOR_DECISION_SCHEMA}'.",
                    supervisor.id,
                    "config.decision_schema",
                )
            )

        finish_edges = outgoing[supervisor.id]
        finish_node = by_id.get(finish_edges[0].target) if len(finish_edges) == 1 else None
        finish_path = config.finish_output.to_state
        if (
            len(finish_edges) != 1
            or finish_node is None
            or finish_node.type != NodeType.OUTPUT
            or not isinstance(finish_node.config, OutputNodeConfig)
            or not _output_reads_path(finish_node.config, finish_path)
        ):
            issues.append(
                _issue(
                    "workflow.supervisor.invalid_mapping",
                    "Supervisor finish_output must feed its single outgoing OUTPUT node.",
                    supervisor.id,
                    "config.finish_output",
                )
            )

        for target_id, target_binding in config.allowed_targets.items():
            issues.extend(
                _target_errors(
                    supervisor.id,
                    target_id,
                    target_binding.task_field,
                    by_id,
                    declared_by_id,
                    incoming,
                    outgoing,
                )
            )
    return issues


def validate_supervisor_parallel_region(
    nodes: list[ValidatedWorkflowNode],
    region: ParallelRegion | None,
) -> list[WorkflowValidationIssue]:
    """Reject a supervisor inside the first runtime's static parallel region."""
    if region is None:
        return []
    region_nodes = {
        region.split_node_id,
        region.join_node_id,
        *(node_id for branch in region.branches for node_id in branch.node_ids),
    }
    return [
        _issue(
            "workflow.supervisor.parallel_not_supported",
            "A supervisor cannot execute inside a V2.3 parallel region.",
            node.id,
            "nodes",
        )
        for node in nodes
        if isinstance(node.config, SupervisorNodeConfig) and node.id in region_nodes
    ]


def _target_errors(
    supervisor_id: str,
    target_id: str,
    task_field: str,
    nodes: dict[str, ValidatedWorkflowNode],
    declared_nodes: dict[str, WorkflowNode],
    incoming: dict[str, list[WorkflowEdge]],
    outgoing: dict[str, list[WorkflowEdge]],
) -> list[WorkflowValidationIssue]:
    field = f"config.allowed_targets.{target_id}"
    if target_id == supervisor_id:
        return [
            _issue(
                "workflow.supervisor.self_target",
                f"Supervisor '{supervisor_id}' cannot target itself.",
                supervisor_id,
                field,
            )
        ]
    declared_target = declared_nodes.get(target_id)
    if declared_target is None:
        return [
            _issue(
                "workflow.supervisor.unknown_target",
                f"Supervisor target '{target_id}' does not reference a workflow node.",
                supervisor_id,
                field,
            )
        ]
    if declared_target.type != NodeType.AGENT:
        return [
            _issue(
                "workflow.supervisor.invalid_target_type",
                f"Supervisor target '{target_id}' must be an AGENT node.",
                supervisor_id,
                field,
            )
        ]
    target = nodes.get(target_id)
    if target is None:
        # The target's own typed-config issue already explains why it cannot be validated.
        return []
    if not isinstance(target.config, AgentNodeConfig):
        raise TypeError(f"Agent target '{target_id}' has an inconsistent validated config.")

    problem = None
    if target.config.inputs is None:
        problem = "must use structured inputs"
    elif task_field in target.config.inputs:
        problem = f"task field '{task_field}' conflicts with a state input"
    elif incoming[target_id] or outgoing[target_id]:
        problem = "must not have ordinary workflow edges"
    if problem is None:
        return []
    return [
        _issue(
            "workflow.supervisor.invalid_mapping",
            f"Supervisor target '{target_id}' {problem}.",
            supervisor_id,
            field,
        )
    ]


def _output_reads_path(config: OutputNodeConfig, path: str) -> bool:
    """Return whether one output node exposes the supervisor's finish destination."""
    if config.output_path == path:
        return True
    return config.outputs is not None and any(
        reference.from_state == path for reference in config.outputs.values()
    )


def _issue(
    code: str,
    message: str,
    node_id: str,
    field: str,
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id, field=field)
