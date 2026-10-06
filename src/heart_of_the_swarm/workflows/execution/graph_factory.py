from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, NoReturn, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.configs import SubworkflowNodeConfig
from heart_of_the_swarm.workflows.documents import WorkflowVersionSnapshot
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.agent_versions import AgentVersionResolver
from heart_of_the_swarm.workflows.execution.errors import (
    WorkflowExecutionError,
    WorkflowExecutionIssue,
)
from heart_of_the_swarm.workflows.execution.models import ExecutionResult
from heart_of_the_swarm.workflows.execution.node_runner import WorkflowNodeRunner
from heart_of_the_swarm.workflows.execution.workflow_versions import WorkflowVersionResolver
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
)
from heart_of_the_swarm.workflows.state import StatePathError, WorkflowState, get_path, set_path
from heart_of_the_swarm.workflows.subworkflows import validate_subworkflow_mappings
from heart_of_the_swarm.workflows.validation import WorkflowValidator


class _GraphState(TypedDict, total=False):
    data: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str
    output: Any
    loop_iterations: dict[str, int]
    parent_frames: tuple["_ExecutionFrame", ...]


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
            subworkflows={},
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
    ) -> WorkflowGraph:
        """Compile one workflow and its immutable dependency tree recursively."""
        subworkflows: dict[str, WorkflowGraph] = {}
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
            subworkflows=subworkflows,
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
        subworkflows: Mapping[str, WorkflowGraph],
        compile_interrupt_after: tuple[str, ...] = (),
    ) -> WorkflowGraph:
        """Build one graph from validated nodes and already-compiled child graphs."""

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
        )
        outgoing = self._outgoing_edges(workflow)
        graph_node_ids = {node.id: self._graph_node_id(node.id) for node in workflow.nodes}
        builder = StateGraph(_GraphState)

        for node in workflow.nodes:
            graph_node_id = graph_node_ids[node.id]
            edges = outgoing[node.id]
            if isinstance(node.config, SubworkflowNodeConfig):
                builder.add_node(
                    graph_node_id,
                    self._subworkflow_adapter(
                        workflow,
                        node,
                        subworkflows[node.id],
                        runner,
                    ),
                )
            else:
                builder.add_node(graph_node_id, self._node_action(runner, node, edges))
            if node.type == NodeType.CONDITION:
                targets = {edge.target: graph_node_ids[edge.target] for edge in edges}
                builder.add_conditional_edges(graph_node_id, self._selected_target, targets)
            elif node.type == NodeType.OUTPUT:
                builder.add_edge(graph_node_id, END)
            else:
                builder.add_edge(graph_node_id, graph_node_ids[edges[0].target])

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
    ) -> CompiledStateGraph:
        """Build a state-isolating wrapper around one native LangGraph subgraph."""
        config = node.config
        if not isinstance(config, SubworkflowNodeConfig):
            raise TypeError(f"Node '{node.id}' has an inconsistent subworkflow configuration.")

        async def enter(state: _GraphState) -> _GraphState:
            await runner.record_started(node)
            try:
                child_input = {
                    name: deepcopy(get_path(state["data"], binding.from_state))
                    for name, binding in config.inputs.items()
                }
            except StatePathError as exc:
                code = "workflow.execution.subworkflow_input_missing"
                message = f"Input for subworkflow node '{node.id}' could not be resolved."
                await runner.record_failed(
                    node,
                    error_code=code,
                    safe_message=message,
                )
                cls._raise_subworkflow_error(code, message, workflow, node, exc)
            frame: _ExecutionFrame = {
                "data": deepcopy(state["data"]),
                "executed_nodes": tuple(state.get("executed_nodes", ())),
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
            await runner.record_completed(node)
            return {
                "data": updated,
                "executed_nodes": (*frame["executed_nodes"], node.id),
                "loop_iterations": frame["loop_iterations"],
                "parent_frames": frames[:-1],
            }

        adapter = StateGraph(_GraphState)
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
    def _selected_target(state: _GraphState) -> str:
        """Return the route selected by the condition node that just executed."""
        return state["selected_target"]
