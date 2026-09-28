from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ValidationError

from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConditionNodeConfig,
    InputNodeConfig,
    LLMNodeConfig,
    NodeConfig,
    OutputNodeConfig,
    ToolNodeConfig,
    TransformNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from heart_of_the_swarm.workflows.state import StateReference
from heart_of_the_swarm.workflows.validation.errors import (
    WorkflowValidationError,
    WorkflowValidationIssue,
)
from heart_of_the_swarm.workflows.validation.graph import (
    adjacency,
    find_cycle,
    nodes_reaching_outputs,
    reachable_nodes,
)

_CONFIG_TYPES: dict[NodeType, type[NodeConfig]] = {
    NodeType.INPUT: InputNodeConfig,
    NodeType.AGENT: AgentNodeConfig,
    NodeType.LLM: LLMNodeConfig,
    NodeType.TOOL: ToolNodeConfig,
    NodeType.CONDITION: ConditionNodeConfig,
    NodeType.TRANSFORM: TransformNodeConfig,
    NodeType.OUTPUT: OutputNodeConfig,
}


class WorkflowValidator:
    def __init__(self, tool_names: Iterable[str], provider_names: Iterable[str]) -> None:
        self.tool_names = frozenset(tool_names)
        self.provider_names = frozenset(provider_names)

    def validate(self, spec: WorkflowSpec) -> ValidatedWorkflowSpec:
        issues: list[WorkflowValidationIssue] = []
        issues.extend(self._structural_errors(spec))
        typed_nodes, config_issues = self._parse_nodes(spec.nodes)
        issues.extend(config_issues)
        issues.extend(self._graph_errors(spec))
        issues.extend(self._semantic_errors(spec, typed_nodes))
        if issues:
            raise WorkflowValidationError(issues)
        return ValidatedWorkflowSpec(
            schema_version=spec.schema_version,
            id=spec.id,
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            nodes=typed_nodes,
            edges=spec.edges,
            entrypoint=spec.entrypoint,
        )

    @staticmethod
    def _structural_errors(spec: WorkflowSpec) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        counts = Counter(node.id for node in spec.nodes)
        for node_id, count in counts.items():
            if count > 1:
                issues.append(
                    _issue(
                        "workflow.node.duplicate_id",
                        f"Node ID '{node_id}' is used {count} times.",
                        node_id,
                        "nodes",
                    )
                )
        node_ids = set(counts)
        if spec.entrypoint not in node_ids:
            issues.append(
                _issue(
                    "workflow.entrypoint.unknown",
                    f"Entrypoint '{spec.entrypoint}' does not reference a node.",
                    field="entrypoint",
                )
            )
        for index, edge in enumerate(spec.edges):
            if edge.source not in node_ids:
                issues.append(
                    _issue(
                        "workflow.edge.unknown_source",
                        f"Edge source '{edge.source}' does not reference a node.",
                        field=f"edges.{index}.source",
                    )
                )
            if edge.target not in node_ids:
                issues.append(
                    _issue(
                        "workflow.edge.unknown_target",
                        f"Edge target '{edge.target}' does not reference a node.",
                        field=f"edges.{index}.target",
                    )
                )
        return issues

    def _parse_nodes(
        self, nodes: list[WorkflowNode]
    ) -> tuple[list[ValidatedWorkflowNode], list[WorkflowValidationIssue]]:
        typed: list[ValidatedWorkflowNode] = []
        issues: list[WorkflowValidationIssue] = []
        for node in nodes:
            config_type = _CONFIG_TYPES[node.type]
            try:
                config = config_type.model_validate(node.config)
            except ValidationError as exc:
                for error in exc.errors(include_url=False):
                    location = ".".join(str(part) for part in error["loc"])
                    issues.append(
                        _issue(
                            "workflow.node.invalid_config",
                            error["msg"],
                            node.id,
                            f"config.{location}" if location else "config",
                        )
                    )
                continue
            issues.extend(self._reference_errors(config, node.id))
            typed.append(
                ValidatedWorkflowNode(
                    id=node.id,
                    type=node.type,
                    name=node.name,
                    config=config,
                )
            )
        return typed, issues

    @staticmethod
    def _reference_errors(config: BaseModel, node_id: str) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []

        def inspect(value: Any, field: str) -> None:
            if isinstance(value, dict):
                if set(value) == {"from_state"}:
                    try:
                        StateReference.model_validate(value)
                    except ValidationError as exc:
                        issues.append(
                            _issue(
                                "workflow.state.invalid_reference",
                                exc.errors(include_url=False)[0]["msg"],
                                node_id,
                                field,
                            )
                        )
                    return
                for key, item in value.items():
                    inspect(item, f"{field}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    inspect(item, f"{field}.{index}")

        inspect(config.model_dump(mode="python"), "config")
        return issues

    @staticmethod
    def _graph_errors(spec: WorkflowSpec) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        unique_nodes = {node.id: node for node in spec.nodes}
        valid_edges = [
            edge
            for edge in spec.edges
            if edge.source in unique_nodes and edge.target in unique_nodes
        ]
        incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
        outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in valid_edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)

        for node in unique_nodes.values():
            node_incoming = incoming[node.id]
            node_outgoing = outgoing[node.id]
            conditional = [edge for edge in node_outgoing if edge.condition is not None]
            fallback = [edge for edge in node_outgoing if edge.condition is None]
            if node.type == NodeType.INPUT and node_incoming:
                issues.append(
                    _issue(
                        "workflow.input.has_incoming_edge",
                        f"Input node '{node.id}' cannot have incoming edges.",
                        node.id,
                        "edges",
                    )
                )
            if node.type == NodeType.OUTPUT and node_outgoing:
                issues.append(
                    _issue(
                        "workflow.output.has_outgoing_edge",
                        f"Output node '{node.id}' cannot have outgoing edges.",
                        node.id,
                        "edges",
                    )
                )
            if node.type == NodeType.CONDITION:
                if not conditional:
                    issues.append(
                        _issue(
                            "workflow.condition.missing_branch",
                            f"Condition node '{node.id}' requires a conditional edge.",
                            node.id,
                            "edges",
                        )
                    )
                if not fallback:
                    issues.append(
                        _issue(
                            "workflow.condition.missing_fallback",
                            f"Condition node '{node.id}' requires an unconditional fallback edge.",
                            node.id,
                            "edges",
                        )
                    )
                elif len(fallback) > 1:
                    issues.append(
                        _issue(
                            "workflow.condition.multiple_fallbacks",
                            f"Condition node '{node.id}' has multiple fallback edges.",
                            node.id,
                            "edges",
                        )
                    )
            else:
                if conditional:
                    issues.append(
                        _issue(
                            "workflow.edge.condition_not_allowed",
                            f"Only condition nodes may have conditional edges; found '{node.id}'.",
                            node.id,
                            "edges",
                        )
                    )
                if len(fallback) > 1:
                    issues.append(
                        _issue(
                            "workflow.node.multiple_successors",
                            f"Node '{node.id}' has multiple unconditional outgoing edges.",
                            node.id,
                            "edges",
                        )
                    )

        graph = adjacency(unique_nodes, valid_edges)
        cycle = find_cycle(graph)
        if cycle:
            issues.append(
                _issue(
                    "workflow.cycle_detected",
                    f"Workflow contains a cycle: {' -> '.join(cycle)}.",
                    field="edges",
                )
            )
        reachable = reachable_nodes(graph, spec.entrypoint)
        for node_id in sorted(set(unique_nodes) - reachable):
            issues.append(
                _issue(
                    "workflow.node.unreachable",
                    f"Node '{node_id}' is unreachable from the entrypoint.",
                    node_id,
                    "nodes",
                )
            )
        outputs = [node.id for node in unique_nodes.values() if node.type == NodeType.OUTPUT]
        reaching_outputs = nodes_reaching_outputs(graph, outputs)
        for node_id in sorted(reachable - reaching_outputs):
            issues.append(
                _issue(
                    "workflow.node.no_output_path",
                    f"Node '{node_id}' cannot reach an output node.",
                    node_id,
                    "edges",
                )
            )
        return issues

    def _semantic_errors(
        self, spec: WorkflowSpec, nodes: list[ValidatedWorkflowNode]
    ) -> list[WorkflowValidationIssue]:
        issues: list[WorkflowValidationIssue] = []
        by_id = {node.id: node for node in nodes}
        entrypoint = by_id.get(spec.entrypoint)
        if entrypoint is not None and entrypoint.type != NodeType.INPUT:
            issues.append(
                _issue(
                    "workflow.entrypoint.not_input",
                    "The workflow entrypoint must be an input node.",
                    entrypoint.id,
                    "entrypoint",
                )
            )
        if not any(node.type == NodeType.OUTPUT for node in spec.nodes):
            issues.append(
                _issue(
                    "workflow.output.missing",
                    "The workflow requires at least one output node.",
                    field="nodes",
                )
            )
        issues.extend(_schema_errors(spec.input_schema, "input_schema"))
        issues.extend(_schema_errors(spec.output_schema, "output_schema"))
        for node in nodes:
            config = node.config
            if isinstance(config, AgentNodeConfig):
                issues.extend(self._provider_errors(node.id, config.model.provider))
                for tool in config.tools:
                    if tool not in self.tool_names:
                        issues.append(
                            _issue(
                                "workflow.tool.unknown",
                                f"Tool '{tool}' is not registered.",
                                node.id,
                                "config.tools",
                            )
                        )
            elif isinstance(config, LLMNodeConfig):
                issues.extend(self._provider_errors(node.id, config.model.provider))
            elif isinstance(config, ToolNodeConfig) and config.tool not in self.tool_names:
                issues.append(
                    _issue(
                        "workflow.tool.unknown",
                        f"Tool '{config.tool}' is not registered.",
                        node.id,
                        "config.tool",
                    )
                )
        return issues

    def _provider_errors(self, node_id: str, provider: str) -> list[WorkflowValidationIssue]:
        if provider in self.provider_names:
            return []
        return [
            _issue(
                "workflow.provider.unknown",
                f"Provider '{provider}' is not registered.",
                node_id,
                "config.model.provider",
            )
        ]


def _schema_errors(schema: dict[str, Any], field: str) -> list[WorkflowValidationIssue]:
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        return [
            _issue(
                "workflow.schema.invalid",
                f"Invalid JSON Schema: {exc.message}",
                field=field,
            )
        ]
    return []


def _issue(
    code: str,
    message: str,
    node_id: str | None = None,
    field: str | None = None,
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id, field=field)
