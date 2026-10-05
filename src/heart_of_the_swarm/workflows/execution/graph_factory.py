from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.execution.agent_versions import AgentVersionResolver
from heart_of_the_swarm.workflows.execution.models import ExecutionResult
from heart_of_the_swarm.workflows.execution.node_runner import WorkflowNodeRunner
from heart_of_the_swarm.workflows.spec import (
    ValidatedWorkflowNode,
    ValidatedWorkflowSpec,
    WorkflowEdge,
)
from heart_of_the_swarm.workflows.state import WorkflowState


class _GraphState(TypedDict, total=False):
    data: WorkflowState
    executed_nodes: tuple[str, ...]
    selected_target: str
    output: Any


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
        """Run the StateGraph and return the stable workflow result model."""
        if workflow_input is not None and not isinstance(workflow_input, dict):
            entrypoint = next(
                node for node in self.workflow.nodes if node.id == self.workflow.entrypoint
            )
            self.runner.reject_non_object_input(entrypoint)
        configurable: dict[str, Any] = {}
        if thread_id is not None:
            configurable["thread_id"] = thread_id
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        config: dict[str, Any] = {"configurable": configurable}
        if recursion_limit is not None:
            config["recursion_limit"] = recursion_limit
        graph_input = None
        if workflow_input is not None:
            graph_input = {
                "data": deepcopy(workflow_input),
                "executed_nodes": (),
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
        )


class WorkflowGraphFactory:
    """Translate a validated workflow directly into a LangGraph StateGraph."""

    def __init__(
        self,
        *,
        agent_runner: AgentRunner | None = None,
        agent_versions: AgentVersionResolver | None = None,
        capabilities: ToolRegistry | None = None,
    ) -> None:
        self.agent_runner = agent_runner
        self.agent_versions = agent_versions
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
    ) -> WorkflowGraph:
        """Build one graph while resolving registered capabilities up front."""
        if not isinstance(workflow, ValidatedWorkflowSpec):
            raise TypeError("WorkflowGraphFactory requires a ValidatedWorkflowSpec.")

        runner = WorkflowNodeRunner(
            workflow,
            agent_runner=self.agent_runner,
            agent_versions=self.agent_versions,
            capabilities=self.capabilities,
            callback=callback,
            workflow_run_id=workflow_run_id,
            event_sink=event_sink,
        )
        outgoing = self._outgoing_edges(workflow)
        graph_node_ids = {node.id: self._graph_node_id(node.id) for node in workflow.nodes}
        builder = StateGraph(_GraphState)

        for node in workflow.nodes:
            graph_node_id = graph_node_ids[node.id]
            edges = outgoing[node.id]
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
            builder.compile(checkpointer=checkpointer),
            runner,
            checkpointer is not None,
        )

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
            if result.is_output:
                update["output"] = result.output
            return update

        return execute

    @staticmethod
    def _selected_target(state: _GraphState) -> str:
        """Return the route selected by the condition node that just executed."""
        return state["selected_target"]
