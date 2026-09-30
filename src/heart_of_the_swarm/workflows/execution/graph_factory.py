from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from heart_of_the_swarm.observability import RuntimeCallbackHandler
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflows.enums import NodeType
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
    ) -> None:
        self.workflow = workflow
        self.graph = graph
        self.runner = runner

    async def ainvoke(self, workflow_input: WorkflowState) -> ExecutionResult:
        """Run the StateGraph and return the stable workflow result model."""
        if not isinstance(workflow_input, dict):
            entrypoint = next(
                node for node in self.workflow.nodes if node.id == self.workflow.entrypoint
            )
            self.runner.reject_non_object_input(entrypoint)
        result = await self.graph.ainvoke(
            {
                "data": deepcopy(workflow_input),
                "executed_nodes": (),
            }
        )
        return ExecutionResult(
            workflow_id=self.workflow.id,
            output=deepcopy(result["output"]),
            state=deepcopy(result["data"]),
            executed_nodes=tuple(result["executed_nodes"]),
        )


class WorkflowGraphFactory:
    """Translate a validated workflow directly into a LangGraph StateGraph."""

    def __init__(self, tools: ToolRegistry | None = None) -> None:
        self.tools = tools or ToolRegistry([])

    def create(
        self,
        workflow: ValidatedWorkflowSpec,
        *,
        callback: RuntimeCallbackHandler | None = None,
        workflow_run_id: str | None = None,
    ) -> WorkflowGraph:
        """Build one graph while resolving registered capabilities up front."""
        if not isinstance(workflow, ValidatedWorkflowSpec):
            raise TypeError("WorkflowGraphFactory requires a ValidatedWorkflowSpec.")

        unsupported = [
            node for node in workflow.nodes if node.type in {NodeType.AGENT, NodeType.LLM}
        ]
        if unsupported:
            node = unsupported[0]
            raise ValueError(f"Workflow node type '{node.type}' is not supported yet: '{node.id}'.")

        runner = WorkflowNodeRunner(
            workflow,
            self.tools,
            callback=callback,
            workflow_run_id=workflow_run_id,
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
        return WorkflowGraph(workflow, builder.compile(), runner)

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
