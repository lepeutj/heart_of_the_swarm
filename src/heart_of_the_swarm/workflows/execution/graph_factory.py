from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Annotated, Any, NoReturn, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.configs import (
    SubworkflowNodeConfig,
    SupervisorNodeConfig,
    SupervisorTargetConfig,
)
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.agent_versions import AgentVersionResolver
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.execution.models import ExecutionResult
from heart_of_the_swarm.workflows.execution.node_runner import WorkflowNodeRunner
from heart_of_the_swarm.workflows.execution.parallel import (
    ParallelRuntimeBranch,
    ParallelRuntimeRegion,
    build_parallel_runtime_region,
    merge_parallel_states,
)
from heart_of_the_swarm.workflows.execution.workflow_versions import WorkflowVersionResolver
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
)
from heart_of_the_swarm.workflows.state import StatePathError, WorkflowState, get_path, set_path
from heart_of_the_swarm.workflows.subworkflows import validate_subworkflow_mappings
from heart_of_the_swarm.workflows.supervisors import FinishDecision, HandoffDecision
from heart_of_the_swarm.workflows.validation import WorkflowValidator


class _ParallelBranchFrame(TypedDict, total=False):
    data: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str


def _merge_parallel_branch_frames(
    current: dict[str, _ParallelBranchFrame],
    update: dict[str, _ParallelBranchFrame],
) -> dict[str, _ParallelBranchFrame]:
    """Combine isolated branch frames by stable branch ID."""
    return {**current, **update}


class _GraphState(TypedDict, total=False):
    data: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str
    output: Any
    loop_iterations: dict[str, int]
    parent_frames: tuple["_ExecutionFrame", ...]
    handoff_index: int
    handoff_task: str | None
    active_supervisor: str | None
    active_target: str | None
    parallel_branches: Annotated[
        dict[str, _ParallelBranchFrame],
        _merge_parallel_branch_frames,
    ]


class _ParallelBranchOutput(TypedDict):
    parallel_branches: Annotated[
        dict[str, _ParallelBranchFrame],
        _merge_parallel_branch_frames,
    ]


class _ExecutionFrame(TypedDict):
    data: WorkflowState
    executed_nodes: tuple[str, ...]
    loop_iterations: dict[str, int]


class WorkflowGraph:
    """Executable LangGraph workflow with a domain-level invocation contract."""

    def __init__(
        self,
        workflow: ValidatedWorkflowSpec,
        graph: CompiledStateGraph,
        runner: WorkflowNodeRunner,
        checkpointing: bool,
    ) -> None:
        self.workflow = workflow
        self.graph = graph
        self.runner = runner
        self.checkpointing = checkpointing

    async def ainvoke(
        self,
        workflow_input: WorkflowState | None,
        *,
        recursion_limit: int | None = None,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
        interrupt_after: tuple[str, ...] = (),
    ) -> ExecutionResult:
        """Run the graph, resuming the current thread head when a checkpoint is selected."""
        if workflow_input is not None and not isinstance(workflow_input, dict):
            entrypoint = next(
                node for node in self.workflow.nodes if node.id == self.workflow.entrypoint
            )
            self.runner.reject_non_object_input(entrypoint)
        if checkpoint_id is not None and (thread_id is None or not self.checkpointing):
            raise ValueError("checkpoint resume requires a checkpointed execution thread")
        configurable: dict[str, Any] = {}
        if thread_id is not None:
            configurable["thread_id"] = thread_id
        config: dict[str, Any] = {"configurable": configurable}
        if recursion_limit is not None:
            config["recursion_limit"] = recursion_limit
        graph_input = None
        if workflow_input is not None:
            graph_input = {
                "data": deepcopy(workflow_input),
                "executed_nodes": (),
                "loop_iterations": {},
                "handoff_index": 0,
            }
        result = await self.graph.ainvoke(
            graph_input,
            config=config,
            interrupt_after=[f"workflow_node__{node_id}" for node_id in interrupt_after],
        )
        snapshot_config = {"configurable": {"thread_id": thread_id}}
        snapshot = (
            await self.graph.aget_state(snapshot_config)
            if thread_id is not None and self.checkpointing
            else None
        )
        values = snapshot.values if snapshot is not None else result
        saved_config = snapshot.config.get("configurable", {}) if snapshot is not None else {}
        return ExecutionResult(
            workflow_id=self.workflow.id,
            output=deepcopy(values.get("output")),
            state=deepcopy(values["data"]),
            executed_nodes=tuple(values["executed_nodes"]),
            interrupted=bool(snapshot and snapshot.next),
            checkpoint_id=saved_config.get("checkpoint_id"),
            loop_iterations=dict(values.get("loop_iterations", {})),
        )


class WorkflowGraphFactory:
    """Translate a validated workflow directly into a LangGraph StateGraph."""

    def __init__(
        self,
        *,
        agent_runner: AgentRunner | None = None,
        agent_versions: AgentVersionResolver | None = None,
        workflow_versions: WorkflowVersionResolver | None = None,
        validator: WorkflowValidator | None = None,
        capabilities: ToolRegistry | None = None,
    ) -> None:
        self.agent_runner = agent_runner
        self.agent_versions = agent_versions
        self.workflow_versions = workflow_versions
        self.validator = validator
        self.capabilities = capabilities

    def create(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        event_sink: Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]]
        | None = None,
        workflow_version_id: str | None = None,
        execution_path: str = "root",
        max_handoffs: int = 20,
    ) -> WorkflowGraph:
        """Build one graph while resolving registered capabilities up front."""
        if not isinstance(workflow, ValidatedWorkflowSpec):
            raise TypeError("WorkflowGraphFactory requires a ValidatedWorkflowSpec.")
        if any(node.type == NodeType.SUBWORKFLOW for node in workflow.nodes):
            raise RuntimeError("Subworkflow compilation requires WorkflowGraphFactory.acreate().")

        return self._create(
            workflow,
            callback=callback,
            workflow_run_id=workflow_run_id,
            checkpointer=checkpointer,
            event_sink=event_sink,
            workflow_version_id=workflow_version_id,
            execution_path=execution_path,
            runtime_context=None,
            parallel=build_parallel_runtime_region(workflow),
            subworkflows={},
            max_handoffs=max_handoffs,
        )

    async def acreate(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        event_sink: Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]]
        | None = None,
        workflow_version_id: str | None = None,
        max_subworkflow_depth: int = 8,
        max_handoffs: int = 20,
        interrupt_after: tuple[str, ...] = (),
    ) -> WorkflowGraph:
        """Resolve immutable child versions and compile them as nested LangGraph graphs."""
        if not isinstance(workflow, ValidatedWorkflowSpec):
            raise TypeError("WorkflowGraphFactory requires a ValidatedWorkflowSpec.")
        version_stack = (workflow_version_id,) if workflow_version_id is not None else ()
        return await self._acreate(
            workflow,
            callback=callback,
            workflow_run_id=workflow_run_id,
            checkpointer=checkpointer,
            event_sink=event_sink,
            workflow_version_id=workflow_version_id,
            execution_path="root",
            depth=0,
            max_depth=max_subworkflow_depth,
            version_stack=version_stack,
            workflow_stack=(str(workflow.id),),
            interrupt_after=interrupt_after,
            runtime_context=None,
            max_handoffs=max_handoffs,
        )

    async def _acreate(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        callback: RuntimeCallbackHandler | None,
        workflow_run_id: str | None,
        checkpointer: BaseCheckpointSaver | None,
        event_sink: Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]] | None,
        workflow_version_id: str | None,
        execution_path: str,
        depth: int,
        max_depth: int,
        version_stack: tuple[str, ...],
        workflow_stack: tuple[str, ...],
        interrupt_after: tuple[str, ...],
        runtime_context: dict[str, Any] | None,
        max_handoffs: int,
    ) -> WorkflowGraph:
        """Compile one workflow and its immutable dependency tree recursively."""
        subworkflows: dict[str, WorkflowGraph] = {}
        parallel = build_parallel_runtime_region(workflow)
        for node in workflow.nodes:
            if not isinstance(node.config, SubworkflowNodeConfig):
                continue
            child = await self._resolve_child(workflow, node)
            child_version_id = str(child.id)
            child_workflow_id = str(child.workflow_id)
            if child_version_id in version_stack or child_workflow_id in workflow_stack:
                self._raise_subworkflow_error(
                    "workflow.execution.subworkflow_recursion",
                    f"Subworkflow node '{node.id}' creates a recursive dependency.",
                    workflow,
                    node,
                )
            if depth + 1 > max_depth:
                self._raise_subworkflow_error(
                    "workflow.execution.subworkflow_depth",
                    f"Subworkflow node '{node.id}' exceeds the maximum depth of {max_depth}.",
                    workflow,
                    node,
                )
            if self.validator is None:
                self._raise_subworkflow_error(
                    "workflow.execution.subworkflow_validator_unavailable",
                    "Subworkflow validation is not configured for this runtime.",
                    workflow,
                    node,
                )
            if self.capabilities is not None:
                self.capabilities.verify_contracts(child.capability_contracts)
            child_workflow = self.validator.validate(child.spec)
            try:
                validate_subworkflow_mappings(node.config, child_workflow)
            except ValueError as exc:
                self._raise_subworkflow_error(
                    "workflow.execution.subworkflow_mapping_invalid",
                    f"Subworkflow node '{node.id}' has invalid mappings: {exc}",
                    workflow,
                    node,
                    exc,
                )
            child_context = dict(runtime_context or {})
            branch = parallel.branch_for_node(node.id) if parallel is not None else None
            if branch is not None:
                child_context["branch_id"] = branch.id
            child_graph = await self._acreate(
                child_workflow,
                callback=callback,
                workflow_run_id=workflow_run_id,
                checkpointer=None,
                event_sink=event_sink,
                workflow_version_id=child_version_id,
                execution_path=f"{execution_path}/{node.id}",
                depth=depth + 1,
                max_depth=max_depth,
                version_stack=(*version_stack, child_version_id),
                workflow_stack=(*workflow_stack, child_workflow_id),
                interrupt_after=tuple(
                    path.removeprefix(f"{node.id}/")
                    for path in interrupt_after
                    if path.startswith(f"{node.id}/")
                ),
                runtime_context=child_context,
                max_handoffs=max_handoffs,
            )
            subworkflows[node.id] = child_graph

        return self._create(
            workflow,
            callback=callback,
            workflow_run_id=workflow_run_id,
            checkpointer=checkpointer,
            event_sink=event_sink,
            workflow_version_id=workflow_version_id,
            execution_path=execution_path,
            runtime_context=runtime_context,
            parallel=parallel,
            subworkflows=subworkflows,
            max_handoffs=max_handoffs,
            compile_interrupt_after=tuple(path for path in interrupt_after if "/" not in path),
        )

    def _create(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        callback: RuntimeCallbackHandler | None,
        workflow_run_id: str | None,
        checkpointer: BaseCheckpointSaver | None,
        event_sink: Callable[[str, ValidatedWorkflowNode, dict[str, Any]], Awaitable[None]] | None,
        workflow_version_id: str | None,
        execution_path: str,
        runtime_context: dict[str, Any] | None,
        parallel: ParallelRuntimeRegion | None,
        subworkflows: Mapping[str, WorkflowGraph],
        max_handoffs: int,
        compile_interrupt_after: tuple[str, ...] = (),
    ) -> WorkflowGraph:
        """Build one graph from validated nodes and already-compiled child graphs."""
        if not 1 <= max_handoffs <= 100:
            raise ValueError("max_handoffs must be between 1 and 100")

        runner = WorkflowNodeRunner(
            workflow,
            agent_runner=self.agent_runner,
            agent_versions=self.agent_versions,
            capabilities=self.capabilities,
            callback=callback,
            workflow_run_id=workflow_run_id,
            event_sink=event_sink,
            workflow_version_id=workflow_version_id,
            execution_path=execution_path,
            runtime_context=runtime_context,
        )
        outgoing = self._outgoing_edges(workflow)
        nodes_by_id = {node.id: node for node in workflow.nodes}
        graph_node_ids = {node.id: self._graph_node_id(node.id) for node in workflow.nodes}
        supervisor_nodes = {
            node.id: node
            for node in workflow.nodes
            if isinstance(node.config, SupervisorNodeConfig)
        }
        delegated_targets: dict[
            str,
            tuple[ValidatedWorkflowNode, SupervisorTargetConfig],
        ] = {
            target_id: (supervisor, target_config)
            for supervisor in supervisor_nodes.values()
            for target_id, target_config in supervisor.config.allowed_targets.items()
            if isinstance(supervisor.config, SupervisorNodeConfig)
        }
        branch_by_node = (
            {node_id: branch for branch in parallel.branches for node_id in branch.node_ids}
            if parallel is not None
            else {}
        )
        builder = StateGraph(_GraphState)

        for node in workflow.nodes:
            graph_node_id = graph_node_ids[node.id]
            edges = outgoing[node.id]
            branch = branch_by_node.get(node.id)
            if isinstance(node.config, SupervisorNodeConfig):
                finish_target = outgoing[node.id][0].target
                builder.add_node(
                    graph_node_id,
                    self._supervisor_action(
                        runner,
                        node,
                        graph_node_ids,
                        nodes_by_id,
                        finish_target,
                        max_handoffs,
                    ),
                    destinations=tuple(
                        graph_node_ids[target]
                        for target in (*node.config.allowed_targets, finish_target)
                    ),
                )
            elif node.id in delegated_targets:
                supervisor, target_config = delegated_targets[node.id]
                builder.add_node(
                    graph_node_id,
                    self._delegated_agent_action(
                        runner,
                        node,
                        supervisor,
                        target_config,
                        graph_node_ids[supervisor.id],
                    ),
                    destinations=(graph_node_ids[supervisor.id],),
                )
            elif parallel is not None and node.id == parallel.join_node_id:
                builder.add_node(
                    graph_node_id,
                    self._parallel_join_action(runner, node, edges, parallel),
                )
            elif isinstance(node.config, SubworkflowNodeConfig):
                builder.add_node(
                    graph_node_id,
                    self._subworkflow_adapter(
                        workflow,
                        node,
                        subworkflows[node.id],
                        runner,
                        branch=branch,
                    ),
                )
            elif branch is not None:
                builder.add_node(
                    graph_node_id,
                    self._parallel_node_action(runner, node, edges, branch),
                )
            else:
                builder.add_node(graph_node_id, self._node_action(runner, node, edges))

        completion_ids: dict[str, str] = {}
        if parallel is not None:
            # Each branch gets one stable endpoint so conditional branch exits can still
            # participate in a single native LangGraph all-join.
            for index, branch in enumerate(parallel.branches):
                completion_id = f"parallel_complete__{index}"
                completion_ids[branch.id] = completion_id
                builder.add_node(completion_id, self._parallel_branch_completed)

        for node in workflow.nodes:
            graph_node_id = graph_node_ids[node.id]
            edges = outgoing[node.id]
            branch = branch_by_node.get(node.id)
            if isinstance(node.config, SupervisorNodeConfig) or node.id in delegated_targets:
                # Command returned by these nodes owns the dynamic transition.
                continue
            if node.type == NodeType.CONDITION:
                targets = {
                    edge.target: (
                        completion_ids[branch.id]
                        if branch is not None
                        and parallel is not None
                        and edge.target == parallel.join_node_id
                        else graph_node_ids[edge.target]
                    )
                    for edge in edges
                }
                selector = (
                    self._parallel_selected_target(branch.id)
                    if branch is not None
                    else self._selected_target
                )
                builder.add_conditional_edges(graph_node_id, selector, targets)
            elif node.type == NodeType.OUTPUT:
                builder.add_edge(graph_node_id, END)
            elif parallel is not None and node.id == parallel.split_node_id:
                for edge in edges:
                    builder.add_edge(graph_node_id, graph_node_ids[edge.target])
            elif branch is not None and parallel is not None:
                target = edges[0].target
                builder.add_edge(
                    graph_node_id,
                    completion_ids[branch.id]
                    if target == parallel.join_node_id
                    else graph_node_ids[target],
                )
            else:
                builder.add_edge(graph_node_id, graph_node_ids[edges[0].target])

        if parallel is not None:
            builder.add_edge(
                [completion_ids[branch.id] for branch in parallel.branches],
                graph_node_ids[parallel.join_node_id],
            )

        builder.add_edge(START, graph_node_ids[workflow.entrypoint])
        return WorkflowGraph(
            workflow,
            builder.compile(
                checkpointer=checkpointer,
                interrupt_after=[
                    self._graph_node_id(node_id) for node_id in compile_interrupt_after
                ],
            ),
            runner,
            checkpointer is not None,
        )

    async def _resolve_child(
        self,
        workflow: ValidatedWorkflowSpec,
        node: ValidatedWorkflowNode,
    ) -> WorkflowVersionSnapshot:
        """Resolve one exact child version through the injected runtime boundary."""
        if self.workflow_versions is None:
            raise WorkflowExecutionError(
                WorkflowExecutionIssue(
                    code="workflow.execution.subworkflow_resolver_unavailable",
                    message="Subworkflow version resolution is not configured for this runtime.",
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_type=node.type,
                )
            )
        config = node.config
        if not isinstance(config, SubworkflowNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent subworkflow configuration.")
        child = await self.workflow_versions.resolve(config.workflow_version_id)
        if child is None:
            raise WorkflowExecutionError(
                WorkflowExecutionIssue(
                    code="workflow.execution.subworkflow_version_not_found",
                    message=f"Workflow version '{config.workflow_version_id}' was not found.",
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_type=node.type,
                )
            )
        return child

    @classmethod
    def _subworkflow_adapter(
        cls,
        workflow: ValidatedWorkflowSpec,
        node: ValidatedWorkflowNode,
        child: WorkflowGraph,
        runner: WorkflowNodeRunner,
        *,
        branch: ParallelRuntimeBranch | None = None,
    ) -> CompiledStateGraph:
        """Build a state-isolating wrapper around one native LangGraph subgraph."""
        config = node.config
        if not isinstance(config, SubworkflowNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent subworkflow configuration.")
        event_context = {"branch_id": branch.id} if branch is not None else None

        async def enter(state: _GraphState) -> _GraphState:
            await runner.record_started(node, event_context)
            branch_frame = (
                state.get("parallel_branches", {}).get(branch.id, {}) if branch is not None else {}
            )
            parent_data = branch_frame.get("data", state["data"])
            parent_executed = tuple(
                branch_frame.get("executed_nodes", ())
                if branch is not None
                else state.get("executed_nodes", ())
            )
            try:
                child_input = {
                    name: deepcopy(get_path(parent_data, binding.from_state))
                    for name, binding in config.inputs.items()
                }
            except StatePathError as exc:
                code = "workflow.execution.subworkflow_input_missing"
                message = f"Input for subworkflow node '{node.id}' could not be resolved."
                await runner.record_failed(
                    node,
                    error_code=code,
                    safe_message=message,
                    context=event_context,
                )
                cls._raise_subworkflow_error(code, message, workflow, node, exc)
            frame: _ExecutionFrame = {
                "data": deepcopy(parent_data),
                "executed_nodes": parent_executed,
                "loop_iterations": dict(state.get("loop_iterations", {})),
            }
            return {
                "data": child_input,
                "executed_nodes": (),
                "loop_iterations": {},
                "parent_frames": (*state.get("parent_frames", ()), frame),
            }

        async def exit_child(state: _GraphState) -> _GraphState:
            child_output = state.get("output")
            if not isinstance(child_output, Mapping):
                code = "workflow.execution.subworkflow_output_invalid"
                message = f"Subworkflow node '{node.id}' did not return an object."
                await runner.record_failed(
                    node,
                    error_code=code,
                    safe_message=message,
                    context=event_context,
                )
                cls._raise_subworkflow_error(code, message, workflow, node)
            missing = set(config.outputs) - set(child_output)
            if missing:
                missing_names = ", ".join(sorted(missing))
                code = "workflow.execution.subworkflow_output_missing"
                message = f"Subworkflow node '{node.id}' output is missing: {missing_names}."
                await runner.record_failed(
                    node,
                    error_code=code,
                    safe_message=message,
                    context=event_context,
                )
                cls._raise_subworkflow_error(code, message, workflow, node)
            frames = state.get("parent_frames", ())
            if not frames:
                cls._raise_subworkflow_error(
                    "workflow.execution.subworkflow_frame_missing",
                    f"Subworkflow node '{node.id}' lost its parent execution frame.",
                    workflow,
                    node,
                )
            frame = frames[-1]
            updated = deepcopy(frame["data"])
            for name, binding in config.outputs.items():
                set_path(updated, binding.to_state, deepcopy(child_output[name]))
            await runner.record_completed(node, event_context)
            if branch is not None:
                return {
                    "parallel_branches": {
                        branch.id: {
                            "data": updated,
                            "executed_nodes": (*frame["executed_nodes"], node.id),
                        }
                    },
                    "parent_frames": frames[:-1],
                }
            return {
                "data": updated,
                "executed_nodes": (*frame["executed_nodes"], node.id),
                "loop_iterations": frame["loop_iterations"],
                "parent_frames": frames[:-1],
            }

        adapter = StateGraph(
            _GraphState,
            output_schema=_ParallelBranchOutput if branch is not None else None,
        )
        adapter.add_node("enter", enter)
        adapter.add_node("child", child.graph)
        adapter.add_node("exit", exit_child)
        adapter.add_edge(START, "enter")
        adapter.add_edge("enter", "child")
        adapter.add_edge("child", "exit")
        adapter.add_edge("exit", END)
        return adapter.compile(checkpointer=None)

    @staticmethod
    def _raise_subworkflow_error(
        code: str,
        message: str,
        workflow: ValidatedWorkflowSpec,
        node: ValidatedWorkflowNode,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise a normalized execution failure with parent node context."""
        error = WorkflowExecutionError(
            WorkflowExecutionIssue(
                code=code,
                message=message,
                workflow_id=workflow.id,
                node_id=node.id,
                node_type=node.type,
            )
        )
        if cause is None:
            raise error
        raise error from cause

    @staticmethod
    def _outgoing_edges(workflow: ValidatedWorkflowSpec) -> dict[str, list[WorkflowEdge]]:
        """Group validated edges in their declared order for graph construction."""
        outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in workflow.edges:
            outgoing[edge.source].append(edge)
        return outgoing

    @staticmethod
    def _graph_node_id(node_id: str) -> str:
        """Namespace public node IDs away from LangGraph state and reserved identifiers."""
        return f"workflow_node__{node_id}"

    @staticmethod
    def _node_action(
        runner: WorkflowNodeRunner,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
    ) -> Callable[[_GraphState], Awaitable[_GraphState]]:
        """Adapt one product node to the LangGraph state-update contract."""

        async def execute(state: _GraphState) -> _GraphState:
            result = await runner.run(
                node,
                outgoing_edges,
                state["data"],
                tuple(state.get("executed_nodes", ())),
            )
            update: _GraphState = {
                "data": result.state,
                "executed_nodes": result.executed_nodes,
            }
            if result.selected_target is not None:
                update["selected_target"] = result.selected_target
                selected_edge = next(
                    edge for edge in outgoing_edges if edge.target == result.selected_target
                )
                if selected_edge.loop is not None:
                    iterations = dict(state.get("loop_iterations", {}))
                    next_iteration = iterations.get(selected_edge.loop.id, 0) + 1
                    if next_iteration > selected_edge.loop.max_iterations:
                        raise WorkflowExecutionError(
                            WorkflowExecutionIssue(
                                code="workflow.execution.iteration_limit",
                                message=(
                                    f"Loop '{selected_edge.loop.id}' exceeded its limit of "
                                    f"{selected_edge.loop.max_iterations} iterations."
                                ),
                                workflow_id=runner.workflow.id,
                                node_id=node.id,
                                node_type=node.type,
                            )
                        )
                    iterations[selected_edge.loop.id] = next_iteration
                    update["loop_iterations"] = iterations
            if result.is_output:
                update["output"] = result.output
            return update

        return execute

    @staticmethod
    def _supervisor_action(
        runner: WorkflowNodeRunner,
        node: ValidatedWorkflowNode,
        graph_node_ids: Mapping[str, str],
        nodes_by_id: Mapping[str, ValidatedWorkflowNode],
        finish_target: str,
        max_handoffs: int,
    ) -> Callable[[_GraphState], Awaitable[Command]]:
        """Translate one validated supervisor decision into a native LangGraph command."""
        config = node.config
        if not isinstance(config, SupervisorNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent supervisor configuration.")

        async def execute(state: _GraphState) -> Command:
            active_target = state.get("active_target")
            if active_target is not None and state.get("active_supervisor") == node.id:
                await runner.record_event(
                    "handoff.completed",
                    nodes_by_id[active_target],
                    {
                        "supervisor_node_id": node.id,
                        "target_node_id": active_target,
                        "handoff_index": int(state.get("handoff_index", 0)),
                    },
                )
            result = await runner.run(
                node,
                [],
                state["data"],
                tuple(state.get("executed_nodes", ())),
            )
            decision = result.supervisor_decision
            if decision is None:
                raise TypeError(f"Supervisor node '{node.id}' returned no decision.")
            if isinstance(decision.root, FinishDecision):
                updated = deepcopy(result.state)
                set_path(updated, config.finish_output.to_state, deepcopy(decision.root.result))
                return Command(
                    update={
                        "data": updated,
                        "executed_nodes": result.executed_nodes,
                        "handoff_task": None,
                        "active_supervisor": None,
                        "active_target": None,
                    },
                    goto=graph_node_ids[finish_target],
                )

            if not isinstance(decision.root, HandoffDecision):
                raise TypeError(f"Supervisor node '{node.id}' returned an unknown decision.")
            handoff_index = int(state.get("handoff_index", 0)) + 1
            event_data = {
                "supervisor_node_id": node.id,
                "target_node_id": decision.root.target,
                "handoff_index": handoff_index,
            }
            if handoff_index > max_handoffs:
                await runner.record_event("handoff.limit_reached", node, event_data)
                raise WorkflowExecutionError(
                    WorkflowExecutionIssue(
                        code="workflow.execution.handoff_limit_reached",
                        message=f"Supervisor '{node.id}' exceeded {max_handoffs} handoffs.",
                        workflow_id=runner.workflow.id,
                        node_id=node.id,
                        node_type=node.type,
                    )
                )
            await runner.record_event("handoff.started", node, event_data)
            return Command(
                update={
                    "data": result.state,
                    "executed_nodes": result.executed_nodes,
                    "handoff_index": handoff_index,
                    "handoff_task": decision.root.task,
                    "active_supervisor": node.id,
                    "active_target": decision.root.target,
                },
                goto=graph_node_ids[decision.root.target],
            )

        return execute

    @staticmethod
    def _delegated_agent_action(
        runner: WorkflowNodeRunner,
        node: ValidatedWorkflowNode,
        supervisor: ValidatedWorkflowNode,
        target_config: SupervisorTargetConfig,
        supervisor_graph_node_id: str,
    ) -> Callable[[_GraphState], Awaitable[Command]]:
        """Execute one delegated agent task and return control to its supervisor."""

        async def execute(state: _GraphState) -> Command:
            handoff_index = int(state.get("handoff_index", 0))
            event_data = {
                "supervisor_node_id": supervisor.id,
                "target_node_id": node.id,
                "handoff_index": handoff_index,
            }
            try:
                result = await runner.run(
                    node,
                    [],
                    state["data"],
                    tuple(state.get("executed_nodes", ())),
                    event_data,
                    {target_config.task_field: state["handoff_task"]},
                )
            except Exception:
                await runner.record_event("handoff.failed", node, event_data)
                raise
            return Command(
                update={
                    "data": result.state,
                    "executed_nodes": result.executed_nodes,
                },
                goto=supervisor_graph_node_id,
            )

        return execute

    @staticmethod
    def _parallel_node_action(
        runner: WorkflowNodeRunner,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
        branch: ParallelRuntimeBranch,
    ) -> Callable[[_GraphState], Awaitable[_GraphState]]:
        """Execute one node against its branch-local state frame."""

        async def execute(state: _GraphState) -> _GraphState:
            frame = state.get("parallel_branches", {}).get(branch.id, {})
            result = await runner.run(
                node,
                outgoing_edges,
                frame.get("data", deepcopy(state["data"])),
                tuple(frame.get("executed_nodes", ())),
                {"branch_id": branch.id},
            )
            updated: _ParallelBranchFrame = {
                "data": result.state,
                "executed_nodes": result.executed_nodes,
            }
            if result.selected_target is not None:
                updated["selected_target"] = result.selected_target
            return {"parallel_branches": {branch.id: updated}}

        return execute

    @staticmethod
    def _parallel_join_action(
        runner: WorkflowNodeRunner,
        node: ValidatedWorkflowNode,
        outgoing_edges: list[WorkflowEdge],
        region: ParallelRuntimeRegion,
    ) -> Callable[[_GraphState], Awaitable[_GraphState]]:
        """Merge completed branch frames, then execute the public all-join node once."""

        async def execute(state: _GraphState) -> _GraphState:
            frames = state.get("parallel_branches", {})
            missing = [branch.id for branch in region.branches if branch.id not in frames]
            if missing:
                raise WorkflowExecutionError(
                    WorkflowExecutionIssue(
                        code="workflow.execution.parallel_branch_missing",
                        message="Parallel join is missing completed branches: "
                        + ", ".join(missing),
                        workflow_id=runner.workflow.id,
                        node_id=node.id,
                        node_type=node.type,
                    )
                )
            branch_states = {branch.id: frames[branch.id]["data"] for branch in region.branches}
            branch_nodes = {
                branch.id: tuple(frames[branch.id].get("executed_nodes", ()))
                for branch in region.branches
            }
            try:
                merged = merge_parallel_states(
                    state["data"],
                    region,
                    branch_states,
                    branch_nodes,
                )
            except (StatePathError, TypeError, ValueError) as exc:
                raise WorkflowExecutionError(
                    WorkflowExecutionIssue(
                        code="workflow.execution.parallel_reduction_failed",
                        message=f"Parallel state reduction failed at join '{node.id}': {exc}.",
                        workflow_id=runner.workflow.id,
                        node_id=node.id,
                        node_type=node.type,
                    )
                ) from exc
            executed_nodes = tuple(state.get("executed_nodes", ())) + tuple(
                node_id for branch in region.branches for node_id in branch_nodes[branch.id]
            )
            result = await runner.run(node, outgoing_edges, merged, executed_nodes)
            update: _GraphState = {
                "data": result.state,
                "executed_nodes": result.executed_nodes,
            }
            if result.selected_target is not None:
                update["selected_target"] = result.selected_target
            if result.is_output:
                update["output"] = result.output
            return update

        return execute

    @staticmethod
    async def _parallel_branch_completed(_state: _GraphState) -> _GraphState:
        """Mark one branch endpoint for LangGraph's grouped all-join edge."""
        return {}

    @staticmethod
    def _parallel_selected_target(
        branch_id: str,
    ) -> Callable[[_GraphState], str]:
        """Read a condition route from one isolated branch frame."""

        def selected(state: _GraphState) -> str:
            return state["parallel_branches"][branch_id]["selected_target"]

        return selected

    @staticmethod
    def _selected_target(state: _GraphState) -> str:
        """Return the route selected by the condition node that just executed."""
        return state["selected_target"]
