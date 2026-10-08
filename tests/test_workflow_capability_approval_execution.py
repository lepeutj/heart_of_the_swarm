from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import select

from heart_of_the_swarm.authorization import (
    AuthorizationDecision,
    AuthorizationService,
    ConfiguredCapabilityPolicySource,
    PolicyKey,
    Principal,
    PrincipalKind,
)
from heart_of_the_swarm.capability_authorization import CapabilityAuthorizer
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.models import ApprovalGrantRecord, WorkflowInterruptionRecord
from heart_of_the_swarm.repositories import WorkflowRepository, WorkflowRunRepository
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import ToolRegistry
from heart_of_the_swarm.workflow_approvals import WorkflowApprovalService
from heart_of_the_swarm.workflow_execution import WorkflowExecutor, WorkflowRunService
from heart_of_the_swarm.workflows import WorkflowSpec, WorkflowValidator
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave
from heart_of_the_swarm.workflows.execution.checkpoints import DurableWorkflowCheckpoints


def capability_approval_spec() -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "3c0e3462-7fcf-4636-81d7-e98a3e7cba89",
            "name": "Approved publication",
            "description": "Prepare and publish one value after approval.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "string"},
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "prepare",
                    "type": "transform",
                    "name": "Prepare",
                    "config": {"assign": {"$.prepared": "report"}},
                },
                {
                    "id": "publish",
                    "type": "connector",
                    "name": "Publish",
                    "config": {
                        "capability_id": "record_publish",
                        "inputs": {"value": {"from_state": "$.prepared"}},
                        "outputs": {"result": {"to_state": "$.result"}},
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {"output_path": "$.result"},
                },
            ],
            "edges": [
                {"source": "input", "target": "prepare"},
                {"source": "prepare", "target": "publish"},
                {"source": "publish", "target": "output"},
            ],
        }
    )


class DenyPolicySource:
    async def policies_for(self, _request) -> dict[PolicyKey, AuthorizationDecision]:
        return {}


async def _executor(
    database: Database,
    checkpoints: DurableWorkflowCheckpoints,
    settings: Settings,
    validator: WorkflowValidator,
    tools: ToolRegistry,
    authorization: AuthorizationService,
) -> WorkflowExecutor:
    return WorkflowExecutor(
        settings,
        database,
        validator,
        tools,
        agent_runner=None,  # type: ignore[arg-type]
        telemetry=Telemetry(settings),
        checkpoints=checkpoints,
        capability_authorizer=CapabilityAuthorizer(authorization),
    )


async def _interrupted_run(calls: list[str], *, fail_on_call: bool = False):
    database = Database("sqlite+aiosqlite:///:memory:")
    checkpoints = DurableWorkflowCheckpoints("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    await checkpoints.start()

    @tool
    async def record_publish(value: str) -> dict[str, str]:
        """Record one externally visible publication."""
        calls.append(value)
        if fail_on_call:
            raise RuntimeError("external outcome is unknown")
        return {"result": value}

    tools = ToolRegistry([record_publish])
    validator = WorkflowValidator(tools.names, [])
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    async with database.session() as session:
        workflows = WorkflowRepository(session)
        draft = await workflows.save(
            WorkflowDraftSave(spec=capability_approval_spec().model_dump(mode="json"))
        )
        version = await workflows.create_version(str(draft.id))
    assert version is not None
    principal = f"workflow_node:{version.id}/publish"
    authorization = AuthorizationService(
        ConfiguredCapabilityPolicySource({}, {principal: ["record_publish"]})
    )
    runs = WorkflowRunService(database, validator, tools, checkpoints)
    first = await runs.queue(version.id, {}, "trace-capability-approval")
    async with database.session() as session:
        assert await WorkflowRunRepository(session).claim_next("worker-1", 60) == str(first.run_id)
    executor = await _executor(
        database,
        checkpoints,
        settings,
        validator,
        tools,
        authorization,
    )
    await executor.execute(str(first.run_id), "worker-1")
    async with database.session() as session:
        interruption = (
            await session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == str(first.run_id)
                )
            )
        ).scalar_one()
    return database, checkpoints, settings, validator, tools, runs, first, interruption


async def test_connector_approval_resumes_consumes_and_executes_once() -> None:
    calls: list[str] = []
    (
        database,
        checkpoints,
        settings,
        validator,
        tools,
        runs,
        first,
        interruption,
    ) = await _interrupted_run(calls)
    try:
        assert calls == []
        assert interruption.kind == "capability_approval"
        assert interruption.request["arguments_fingerprint"]
        resumed = await WorkflowApprovalService(database, checkpoints).respond(
            UUID(interruption.id),
            {"approved": True},
            "trace-resumed",
            approved_by=Principal(kind=PrincipalKind.USER, id="operator-1"),
        )
        async with database.session() as session:
            assert await WorkflowRunRepository(session).claim_next("worker-2", 60) == str(
                resumed.run_id
            )
        principal = f"workflow_node:{resumed.workflow_version_id}/publish"
        executor = await _executor(
            database,
            checkpoints,
            settings,
            validator,
            tools,
            AuthorizationService(
                ConfiguredCapabilityPolicySource({}, {principal: ["record_publish"]})
            ),
        )
        await executor.execute(str(resumed.run_id), "worker-2")

        completed = await runs.get(resumed.run_id)
        events = await runs.events(resumed.run_id)
        async with database.session() as session:
            grant = (await session.execute(select(ApprovalGrantRecord))).scalar_one()
        assert completed is not None and completed.status == "completed"
        assert completed.output == "report"
        assert calls == ["report"]
        assert grant.status == "consumed"
        assert grant.consumed_by_run_id == str(resumed.run_id)
        started = [
            event.data["node_id"] for event in events or [] if event.event_type == "node.started"
        ]
        assert started == ["publish", "output"]
        assert "input" not in started and "prepare" not in started
    finally:
        await checkpoints.close()
        await database.close()


async def test_policy_deny_after_approval_blocks_connector_and_preserves_grant() -> None:
    calls: list[str] = []
    (
        database,
        checkpoints,
        settings,
        validator,
        tools,
        runs,
        _first,
        interruption,
    ) = await _interrupted_run(calls)
    try:
        resumed = await WorkflowApprovalService(database, checkpoints).respond(
            UUID(interruption.id),
            {"approved": True},
            "trace-denied",
            approved_by=Principal(kind=PrincipalKind.USER, id="operator-1"),
        )
        async with database.session() as session:
            await WorkflowRunRepository(session).claim_next("worker-2", 60)
        executor = await _executor(
            database,
            checkpoints,
            settings,
            validator,
            tools,
            AuthorizationService(DenyPolicySource()),
        )
        await executor.execute(str(resumed.run_id), "worker-2")

        failed = await runs.get(resumed.run_id)
        async with database.session() as session:
            grant = (await session.execute(select(ApprovalGrantRecord))).scalar_one()
        assert failed is not None and failed.status == "failed"
        assert calls == []
        assert grant.status == "active"
        assert grant.consumed_at is None
    finally:
        await checkpoints.close()
        await database.close()


async def test_failure_after_consumption_does_not_release_grant_for_retry() -> None:
    calls: list[str] = []
    (
        database,
        checkpoints,
        settings,
        validator,
        tools,
        runs,
        _first,
        interruption,
    ) = await _interrupted_run(calls, fail_on_call=True)
    try:
        resumed = await WorkflowApprovalService(database, checkpoints).respond(
            UUID(interruption.id),
            {"approved": True},
            "trace-unknown-effect",
            approved_by=Principal(kind=PrincipalKind.USER, id="operator-1"),
        )
        async with database.session() as session:
            await WorkflowRunRepository(session).claim_next("worker-2", 60)
        principal = f"workflow_node:{resumed.workflow_version_id}/publish"
        executor = await _executor(
            database,
            checkpoints,
            settings,
            validator,
            tools,
            AuthorizationService(
                ConfiguredCapabilityPolicySource({}, {principal: ["record_publish"]})
            ),
        )
        await executor.execute(str(resumed.run_id), "worker-2")

        failed = await runs.get(resumed.run_id)
        async with database.session() as session:
            grant = (await session.execute(select(ApprovalGrantRecord))).scalar_one()
        assert failed is not None and failed.status == "failed"
        assert calls == ["report"]
        assert grant.status == "consumed"
        assert grant.consumed_by_run_id == str(resumed.run_id)
    finally:
        await checkpoints.close()
        await database.close()
