from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ValidationError

from heart_of_the_swarm.workflows.configs import (
    NODE_CONFIG_TYPES,
    AgentNodeConfig,
    ConnectorNodeConfig,
    InlineAgentSource,
    SubworkflowNodeConfig,
    SupervisorNodeConfig,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.spec import (
    StateReducer,
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
from heart_of_the_swarm.workflows.validation.parallel import analyze_parallel_region
from heart_of_the_swarm.workflows.validation.supervisor import (
    supervisor_reachability_edges,
    validate_supervisor_parallel_region,
    validate_supervisors,
)


class WorkflowValidator:
    def __init__(
        self,
        tool_names: Iterable[str] | Callable[[], Iterable[str]],
        provider_names: Iterable[str],
        skill_names: Iterable[str] | Callable[[], Iterable[str]] = (),
    ) -> None:
        self._tool_names = tool_names
        self.provider_names = frozenset(provider_names)
        self._skill_names = skill_names

    @property
    def tool_names(self) -> frozenset[str]:
        names = self._tool_names() if callable(self._tool_names) else self._tool_names
        return frozenset(names)

    @property
    def skill_names(self) -> frozenset[str]:
        names = self._skill_names() if callable(self._skill_names) else self._skill_names
        return frozenset(names)

    def validate(self, spec: WorkflowSpec) -> ValidatedWorkflowSpec:
        issues: list[WorkflowValidationIssue] = []
        issues.extend(self._structural_errors(spec))
        typed_nodes, config_issues = self._parse_nodes(spec.nodes)
        issues.extend(config_issues)
        issues.extend(self._graph_errors(spec, typed_nodes))
        issues.extend(self._semantic_errors(spec, typed_nodes))
        parallel_region, parallel_issues = analyze_parallel_region(spec, typed_nodes)
        issues.extend(parallel_issues)
        issues.extend(validate_supervisor_parallel_region(typed_nodes, parallel_region))
        if issues:
            raise WorkflowValidationError(issues)
        return ValidatedWorkflowSpec(
            schema_version=spec.schema_version,
            id=spec.id,
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            state_schema=spec.state_schema,
            nodes=typed_nodes,
            edges=spec.edges,
            entrypoint=spec.entrypoint,
            parallel_region=parallel_region,
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
            config_type = NODE_CONFIG_TYPES[node.type]
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
    def _graph_errors(
        spec: WorkflowSpec,
        typed_nodes: list[ValidatedWorkflowNode],
    ) -> list[WorkflowValidationIssue]:
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

        loop_edges = [edge for edge in valid_edges if edge.loop is not None]
        if spec.schema_version == "1" and loop_edges:
            issues.append(
                _issue(
                    "workflow.loop.requires_schema_v2",
                    "Loop edges require workflow schema version 2.",
                    field="schema_version",
                )
            )
        if len(loop_edges) > 1:
            issues.append(
                _issue(
                    "workflow.loop.multiple_not_supported",
                    "The first V2 runtime supports exactly one declared loop edge.",
                    field="edges",
                )
            )
        loop_ids = [edge.loop.id for edge in loop_edges if edge.loop is not None]
        if len(loop_ids) != len(set(loop_ids)):
            issues.append(
                _issue(
                    "workflow.loop.duplicate_id",
                    "Loop identifiers must be unique.",
                    field="edges",
                )
            )

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
                targets = [edge.target for edge in node_outgoing]
                if len(targets) != len(set(targets)):
                    issues.append(
                        _issue(
                            "workflow.condition.duplicate_target",
                            f"Condition node '{node.id}' must use a distinct target per route.",
                            node.id,
                            "edges",
                        )
                    )
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
                if len(fallback) > 1 and spec.schema_version == "1":
                    issues.append(
                        _issue(
                            "workflow.node.multiple_successors",
                            f"Node '{node.id}' has multiple unconditional outgoing edges.",
                            node.id,
                            "edges",
                        )
                    )
                if any(edge.loop is not None for edge in node_outgoing):
                    issues.append(
                        _issue(
                            "workflow.loop.source_not_condition",
                            f"Loop edge source '{node.id}' must be a condition node.",
                            node.id,
                            "edges",
                        )
                    )

        routing_edges = [
            *valid_edges,
            *supervisor_reachability_edges(typed_nodes, set(unique_nodes)),
        ]
        graph = adjacency(unique_nodes, routing_edges)
        forward_edges = [edge for edge in valid_edges if edge.loop is None]
        forward_graph = adjacency(unique_nodes, forward_edges)
        cycle = find_cycle(forward_graph)
        if cycle:
            issues.append(
                _issue(
                    "workflow.cycle_detected",
                    f"Workflow contains a cycle: {' -> '.join(cycle)}.",
                    field="edges",
                )
            )
        for index, edge in enumerate(spec.edges):
            if (
                edge.loop is None
                or edge.source not in unique_nodes
                or edge.target not in unique_nodes
            ):
                continue
            reachable_from_target = reachable_nodes(forward_graph, edge.target)
            if edge.source not in reachable_from_target:
                issues.append(
                    _issue(
                        "workflow.loop.not_back_edge",
                        f"Loop edge '{edge.source} -> {edge.target}' does not close a cycle.",
                        edge.source,
                        f"edges.{index}.loop",
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
        if spec.schema_version == "1" and any(
            isinstance(node.config, SubworkflowNodeConfig) for node in nodes
        ):
            issues.append(
                _issue(
                    "workflow.subworkflow.requires_schema_v2",
                    "Subworkflow nodes require workflow schema version 2.",
                    field="schema_version",
                )
            )
        issues.extend(_schema_errors(spec.input_schema, "input_schema"))
        if spec.output_schema is not None:
            issues.extend(_schema_errors(spec.output_schema, "output_schema"))
        issues.extend(_state_schema_errors(spec))
        issues.extend(validate_supervisors(spec, nodes))
        for node in nodes:
            config = node.config
            if isinstance(config, (AgentNodeConfig, SupervisorNodeConfig)):
                if isinstance(config.source, InlineAgentSource):
                    agent = config.source.agent
                    issues.extend(self._provider_errors(node.id, agent.model.provider))
                    for tool in agent.tools:
                        if tool not in self.tool_names:
                            issues.append(
                                _issue(
                                    "workflow.tool.unknown",
                                    f"Tool '{tool}' is not registered.",
                                    node.id,
                                    "config.source.agent.tools",
                                )
                            )
                    for skill in agent.skills:
                        if skill not in self.skill_names:
                            issues.append(
                                _issue(
                                    "workflow.skill.unknown",
                                    f"Skill '{skill}' is not registered.",
                                    node.id,
                                    "config.source.agent.skills",
                                )
                            )
                if isinstance(config, AgentNodeConfig) and config.response_schema is not None:
                    issues.extend(
                        _response_schema_errors(
                            node.id, config.response_schema, config.outputs or {}
                        )
                    )
            elif isinstance(config, ConnectorNodeConfig):
                if config.capability_id not in self.tool_names:
                    issues.append(
                        _issue(
                            "workflow.connector.unknown",
                            f"Capability '{config.capability_id}' is not registered.",
                            node.id,
                            "config.capability_id",
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
                "config.source.agent.model.provider",
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


def _state_schema_errors(spec: WorkflowSpec) -> list[WorkflowValidationIssue]:
    """Validate state-field schemas and reducer compatibility."""
    if not spec.state_schema:
        return []
    issues: list[WorkflowValidationIssue] = []
    if spec.schema_version == "1":
        issues.append(
            _issue(
                "workflow.state_schema.requires_schema_v2",
                "Shared state declarations require workflow schema version 2.",
                field="schema_version",
            )
        )
    for path in spec.state_schema:
        declaration = spec.state_schema[path]
        field = f"state_schema.{path}"
        issues.extend(_schema_errors(declaration.schema_, f"{field}.schema"))
        expected_type = {
            StateReducer.APPEND: "array",
            StateReducer.MERGE_DICT: "object",
        }.get(declaration.reducer)
        if expected_type is not None and declaration.schema_.get("type") != expected_type:
            issues.append(
                _issue(
                    "workflow.state_schema.reducer_type_mismatch",
                    f"Reducer '{declaration.reducer}' at '{path}' requires a JSON Schema of "
                    f"type '{expected_type}'.",
                    field=f"{field}.reducer",
                )
            )
    return issues


def _response_schema_errors(
    node_id: str,
    schema: dict[str, Any],
    outputs: dict[str, Any],
) -> list[WorkflowValidationIssue]:
    issues = _schema_errors(schema, "config.response_schema")
    if issues:
        return [issue.model_copy(update={"node_id": node_id}) for issue in issues]
    if schema.get("type") != "object":
        issues.append(
            _issue(
                "workflow.agent.response_schema_not_object",
                "Agent response_schema must describe an object.",
                node_id,
                "config.response_schema.type",
            )
        )
        return issues
    for metadata_field in ("title", "description"):
        if not isinstance(schema.get(metadata_field), str) or not schema[metadata_field].strip():
            issues.append(
                _issue(
                    "workflow.agent.response_schema_metadata_missing",
                    f"Agent response_schema requires a non-empty {metadata_field}.",
                    node_id,
                    f"config.response_schema.{metadata_field}",
                )
            )
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    for name in outputs:
        if name not in properties:
            issues.append(
                _issue(
                    "workflow.agent.output_not_in_schema",
                    f"Output '{name}' is not declared in response_schema properties.",
                    node_id,
                    f"config.outputs.{name}",
                )
            )
        elif name not in required_names:
            issues.append(
                _issue(
                    "workflow.agent.output_not_required",
                    f"Output '{name}' must be required by response_schema.",
                    node_id,
                    f"config.outputs.{name}",
                )
            )
    return issues


def _issue(
    code: str,
    message: str,
    node_id: str | None = None,
    field: str | None = None,
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id, field=field)
