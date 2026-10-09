from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import InjectedToolCallId, tool

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.models import ExecutionThreadRecord
from heart_of_the_swarm.repositories import WorkflowRunRepository
from heart_of_the_swarm.spec import AgentSpec, ModelConfig
from heart_of_the_swarm.tools import ToolRegistry, create_default_registry
from heart_of_the_swarm.workflows import (
    ExecutionPolicy,
    WorkflowExecutionError,
    WorkflowGraphFactory,
    WorkflowSpec,
    WorkflowValidator,
    WorkflowVersionRunner,
)
from heart_of_the_swarm.workflows.documents import WorkflowVersionDetail
from heart_of_the_swarm.workflows.execution.execution_budget import (
    ExecutionBudgetExceededError,
    InMemoryExecutionBudget,
    ModelCallBudgetCallback,
)

recorded_tool_calls: list[str] = []
recorded_injected_calls: list[tuple[str, str]] = []


@tool
async def record_value(value: str) -> dict[str, str]:
    """Record and return one value."""
    recorded_tool_calls.append(value)
    return {"result": value}


@tool
async def failing_tool(value: str) -> str:
    """Fail after accepting one valid invocation."""
    raise RuntimeError(f"failed: {value}")


@tool
async def tool_with_injected_call_id(
    value: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str:
    """Record one value together with its LangChain-injected call identifier."""
    recorded_injected_calls.append((value, tool_call_id))
    return value


class CountingFakeModel(FakeMessagesListChatModel):
    provider_calls: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CountingFakeModel":
        return self

    def _generate(self, *args: Any, **kwargs: Any):
        self.provider_calls += 1
        return super()._generate(*args, **kwargs)


class StaticProviders:
    def __init__(self, model: CountingFakeModel) -> None:
        self.model = model

    def create_model(self, _config: ModelConfig) -> CountingFakeModel:
        return self.model


class AcceptingValidator:
    def validate_execution(self, spec: AgentSpec) -> AgentSpec:
        return spec


def agent_spec() -> AgentSpec:
    return AgentSpec(
        name="BudgetedAgent",
        goal="Answer one request",
        instructions="Answer directly.",
        model={"provider": "test", "model_id": "fake"},
        tools=[],
    )


async def test_model_budget_blocks_the_next_call_inside_one_agent_tool_loop() -> None:
    model = CountingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": "1 + 1"},
                        "id": "calculator-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="unexpected"),
        ]
    )
    registry = create_default_registry()
    runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviders(model),
        AcceptingValidator(),
        AgentFactory(registry),
    )
    callback = ModelCallBudgetCallback(InMemoryExecutionBudget(1, 10))
    spec = agent_spec().model_copy(update={"tools": ["calculator"]})

    with pytest.raises(ExecutionBudgetExceededError):
        await runner.invoke(spec, "calculate", callbacks=[callback])

    assert model.provider_calls == 1


async def test_tool_budget_blocks_the_next_tool_inside_one_agent_loop() -> None:
    recorded_tool_calls.clear()
    model = CountingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_value",
                        "args": {"value": "first"},
                        "id": "record-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_value",
                        "args": {"value": "second"},
                        "id": "record-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="unexpected"),
        ]
    )
    registry = ToolRegistry([record_value])
    runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviders(model),
        AcceptingValidator(),
        AgentFactory(registry),
    )
    budget = InMemoryExecutionBudget(max_model_calls=10, max_tool_calls=1)
    spec = agent_spec().model_copy(update={"tools": ["record_value"]})

    with pytest.raises(ExecutionBudgetExceededError) as caught:
        await runner.invoke(spec, "record twice", execution_budget=budget)

    assert caught.value.resource == "tool_calls"
    assert recorded_tool_calls == ["first"]
    assert model.provider_calls == 2


async def test_public_tool_validation_excludes_langchain_injected_arguments() -> None:
    recorded_injected_calls.clear()
    model = CountingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "tool_with_injected_call_id",
                        "args": {"value": "accepted"},
                        "id": "injected-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry([tool_with_injected_call_id])
    runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviders(model),
        AcceptingValidator(),
        AgentFactory(registry),
    )
    budget = InMemoryExecutionBudget(max_model_calls=10, max_tool_calls=1)
    spec = agent_spec().model_copy(update={"tools": ["tool_with_injected_call_id"]})

    assert registry.validate_arguments("tool_with_injected_call_id", {"value": "accepted"}) == {
        "value": "accepted"
    }
    assert set(registry.describe("tool_with_injected_call_id").input_schema["properties"]) == {
        "value"
    }

    assert await runner.invoke(spec, "use the tool", execution_budget=budget) == "done"
    assert recorded_injected_calls == [("accepted", "injected-call-1")]
    assert budget.tool_calls == 1


async def test_tool_budget_counts_attempted_execution_but_not_invalid_arguments() -> None:
    registry = ToolRegistry([record_value, failing_tool])
    budget = InMemoryExecutionBudget(max_model_calls=10, max_tool_calls=2)

    with pytest.raises(Exception):  # noqa: B017 - framework chooses the validation error type
        await registry.invoke_one("record_value", {}, execution_budget=budget)
    assert budget.tool_calls == 0

    with pytest.raises(RuntimeError, match="failed"):
        await registry.invoke_one(
            "failing_tool",
            {"value": "attempted"},
            execution_budget=budget,
        )
    assert budget.tool_calls == 1


async def test_persisted_model_budget_is_shared_by_independent_attempts() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    thread_id = str(uuid4())
    now = datetime.now(UTC)
    try:
        async with database.session() as session:
            session.add(
                ExecutionThreadRecord(
                    id=thread_id,
                    workflow_version_id=str(uuid4()),
                    status="active",
                    model_calls=0,
                    tool_calls=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        async with database.session() as session:
            assert await WorkflowRunRepository(session).consume_model_call(thread_id, 2) == 1
        async with database.session() as session:
            assert await WorkflowRunRepository(session).consume_model_call(thread_id, 2) == 2
        async with database.session() as session:
            assert await WorkflowRunRepository(session).consume_model_call(thread_id, 2) is None
            thread = await session.get(ExecutionThreadRecord, thread_id)
            assert thread is not None
            assert thread.model_calls == 2
            assert thread.tool_calls == 0
        async with database.session() as session:
            assert await WorkflowRunRepository(session).consume_tool_call(thread_id, 1) == 1
        async with database.session() as session:
            assert await WorkflowRunRepository(session).consume_tool_call(thread_id, 1) is None
            thread = await session.get(ExecutionThreadRecord, thread_id)
            assert thread is not None
            assert thread.model_calls == 2
            assert thread.tool_calls == 1
    finally:
        await database.close()


async def test_connector_budget_blocks_before_the_second_capability_call() -> None:
    recorded_tool_calls.clear()
    tools = ToolRegistry([record_value])
    spec = WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": str(uuid4()),
            "name": "Two connectors",
            "description": "Exercise the deterministic tool-call boundary.",
            "input_schema": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
            "output_schema": {"type": "string"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "first",
                    "type": "connector",
                    "name": "First connector",
                    "config": {
                        "capability_id": "record_value",
                        "inputs": {"value": {"from_state": "$.request"}},
                        "outputs": {"result": {"to_state": "$.first"}},
                    },
                },
                {
                    "id": "second",
                    "type": "connector",
                    "name": "Second connector",
                    "config": {
                        "capability_id": "record_value",
                        "inputs": {"value": {"from_state": "$.first"}},
                        "outputs": {"result": {"to_state": "$.second"}},
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.second"},
                },
            ],
            "edges": [
                {"source": "input", "target": "first"},
                {"source": "first", "target": "second"},
                {"source": "second", "target": "output"},
            ],
        }
    )
    version = WorkflowVersionDetail(
        id=uuid4(),
        workflow_id=spec.id,
        version=1,
        spec=spec,
        editor={},
        capability_contracts=[tools.contract("record_value")],
        created_at=datetime.now(UTC),
    )
    runner = WorkflowVersionRunner(
        WorkflowValidator(lambda: tools.names, []),
        tools,
        WorkflowGraphFactory(capabilities=tools),
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await runner.run(
            version,
            {"request": "value"},
            ExecutionPolicy(
                timeout_seconds=2,
                recursion_limit=20,
                max_tool_calls=1,
            ),
        )

    assert caught.value.issue.code == "workflow.execution.tool_call_budget_exceeded"
    assert caught.value.issue.node_id == "second"
    assert recorded_tool_calls == ["value"]


async def test_workflow_model_budget_stops_before_the_next_agent_provider_call() -> None:
    model = CountingFakeModel(
        responses=[AIMessage(content="first answer"), AIMessage(content="unexpected")]
    )
    tools = ToolRegistry()
    agent_runner = AgentRunner(  # type: ignore[arg-type]
        StaticProviders(model),
        AcceptingValidator(),
        AgentFactory(tools),
    )
    inline_source = {"type": "inline", "agent": agent_spec().model_dump(mode="json")}
    spec = WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": str(uuid4()),
            "name": "Two model calls",
            "description": "Exercise one thread-wide model-call budget.",
            "input_schema": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
            "output_schema": {"type": "string"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "first",
                    "type": "agent",
                    "name": "First agent",
                    "config": {
                        "source": inline_source,
                        "input_path": "$.request",
                        "output_path": "$.first",
                    },
                },
                {
                    "id": "second",
                    "type": "agent",
                    "name": "Second agent",
                    "config": {
                        "source": inline_source,
                        "input_path": "$.first",
                        "output_path": "$.second",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.second"},
                },
            ],
            "edges": [
                {"source": "input", "target": "first"},
                {"source": "first", "target": "second"},
                {"source": "second", "target": "output"},
            ],
        }
    )
    version = WorkflowVersionDetail(
        id=uuid4(),
        workflow_id=spec.id,
        version=1,
        spec=spec,
        editor={},
        created_at=datetime.now(UTC),
    )
    runner = WorkflowVersionRunner(
        WorkflowValidator(lambda: tools.names, ["test"]),
        tools,
        WorkflowGraphFactory(agent_runner=agent_runner),
    )

    with pytest.raises(WorkflowExecutionError) as caught:
        await runner.run(
            version,
            {"request": "start"},
            ExecutionPolicy(
                timeout_seconds=2,
                recursion_limit=20,
                max_model_calls=1,
            ),
        )

    assert caught.value.issue.code == "workflow.execution.model_call_budget_exceeded"
    assert caught.value.issue.node_id == "second"
    assert model.provider_calls == 1
