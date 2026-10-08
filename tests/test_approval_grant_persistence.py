import pytest
from sqlalchemy import func, select

from heart_of_the_swarm.approval_grants import (
    ApprovalGrantStatus,
    CapabilityApprovalRequest,
    fingerprint_capability_arguments,
)
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationRequest,
    Principal,
    PrincipalKind,
)
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.models import ApprovalGrantRecord
from heart_of_the_swarm.repositories import (
    ApprovalGrantRepository,
    WorkflowInterruptionRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from heart_of_the_swarm.workflows.documents import WorkflowDraftSave


def connector_draft() -> WorkflowDraftSave:
    return WorkflowDraftSave.model_validate(
        {
            "spec": {
                "schema_version": "1",
                "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
                "name": "Approved connector",
                "description": "Exercise one request-bound capability approval.",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "string"},
                "entrypoint": "input",
                "nodes": [
                    {"id": "input", "type": "input", "name": "Input", "config": {}},
                    {
                        "id": "publish",
                        "type": "connector",
                        "name": "Publish",
                        "config": {
                            "capability_id": "remote.publish",
                            "inputs": {"value": "report"},
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
                    {"source": "input", "target": "publish"},
                    {"source": "publish", "target": "output"},
                ],
            },
            "editor": {},
        }
    )


async def pending_capability_approval(database: Database):
    async with database.session() as session:
        workflows = WorkflowRepository(session)
        draft = await workflows.save(connector_draft())
        version = await workflows.create_version(str(draft.id))
        assert version is not None
        runs = WorkflowRunRepository(session)
        queued = await runs.queue(version, "trace-capability-approval", {})
        assert await runs.claim_next("worker", 60) == str(queued.run_id)
        request = AuthorizationRequest(
            principal=Principal(
                kind=PrincipalKind.WORKFLOW_NODE,
                id=f"{version.id}/publish",
            ),
            action=AuthorizationAction.CAPABILITY_INVOKE,
            resource="capability:remote.publish",
            context=AuthorizationContext(
                workflow_version_id=version.id,
                run_id=queued.run_id,
                node_id="publish",
            ),
        )
        fingerprint = fingerprint_capability_arguments({"value": "report"})
        interruption = await WorkflowInterruptionRepository(session).create_capability_approval(
            str(queued.run_id),
            "checkpoint-capability",
            CapabilityApprovalRequest(
                authorization=request,
                arguments_fingerprint=fingerprint,
            ),
        )
        return interruption, request, fingerprint


async def test_approval_creates_continuation_and_exact_grant_in_one_transaction() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        interruption, source_request, fingerprint = await pending_capability_approval(database)
        approver = Principal(kind=PrincipalKind.USER, id="operator-1")
        async with database.session() as session:
            accepted = await WorkflowInterruptionRepository(session).resolve_and_resume(
                str(interruption.id),
                response={"approved": True},
                trace_id="trace-continuation",
                approved_by=approver,
            )
            resolved = await WorkflowInterruptionRepository(session).get(str(interruption.id))
            grant_id = resolved.response["grant_id"] if resolved and resolved.response else None
            grant = await ApprovalGrantRepository(session).get(grant_id)

        assert grant is not None
        assert grant.source_run_id == interruption.workflow_run_id
        assert grant.continuation_run_id == accepted.run_id
        assert grant.interruption_id == interruption.id
        assert grant.approved_by == approver
        assert grant.arguments_fingerprint == fingerprint
        continuation_request = source_request.model_copy(
            update={
                "context": source_request.context.model_copy(update={"run_id": accepted.run_id})
            }
        )
        assert grant.permits(continuation_request, fingerprint)
    finally:
        await database.close()


async def test_grant_consumption_is_exact_and_atomic() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        interruption, source_request, fingerprint = await pending_capability_approval(database)
        async with database.session() as session:
            accepted = await WorkflowInterruptionRepository(session).resolve_and_resume(
                str(interruption.id),
                response={"approved": True},
                trace_id="trace-continuation",
                approved_by=Principal(kind=PrincipalKind.USER, id="operator-1"),
            )
            resolved = await WorkflowInterruptionRepository(session).get(str(interruption.id))
        assert resolved is not None and resolved.response is not None
        grant_id = resolved.response["grant_id"]
        request = source_request.model_copy(
            update={
                "context": source_request.context.model_copy(update={"run_id": accepted.run_id})
            }
        )

        async with database.session() as session:
            repository = ApprovalGrantRepository(session)
            with pytest.raises(ValueError, match="not active"):
                await repository.consume(
                    grant_id,
                    request,
                    fingerprint_capability_arguments({"value": "changed"}),
                )
            consumed = await repository.consume(grant_id, request, fingerprint)
            with pytest.raises(ValueError, match="not active"):
                await repository.consume(grant_id, request, fingerprint)

        assert consumed.status == ApprovalGrantStatus.CONSUMED
        assert consumed.consumed_by_run_id == accepted.run_id
        assert consumed.consumed_at is not None
    finally:
        await database.close()


async def test_rejection_creates_no_grant() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        interruption, _, _ = await pending_capability_approval(database)
        async with database.session() as session:
            await WorkflowInterruptionRepository(session).resolve_and_resume(
                str(interruption.id),
                response={"approved": False},
                trace_id="trace-rejected",
                approved_by=Principal(kind=PrincipalKind.USER, id="operator-1"),
            )
            count = (
                await session.execute(select(func.count()).select_from(ApprovalGrantRecord))
            ).scalar_one()
        assert count == 0
    finally:
        await database.close()


async def test_capability_interruption_rejects_a_resource_not_declared_by_the_node() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    try:
        async with database.session() as session:
            workflows = WorkflowRepository(session)
            draft = await workflows.save(connector_draft())
            version = await workflows.create_version(str(draft.id))
            assert version is not None
            runs = WorkflowRunRepository(session)
            queued = await runs.queue(version, "trace-invalid-resource", {})
            assert await runs.claim_next("worker", 60) == str(queued.run_id)
            request = AuthorizationRequest(
                principal=Principal(
                    kind=PrincipalKind.WORKFLOW_NODE,
                    id=f"{version.id}/publish",
                ),
                action=AuthorizationAction.CAPABILITY_INVOKE,
                resource="capability:remote.delete",
                context=AuthorizationContext(
                    workflow_version_id=version.id,
                    run_id=queued.run_id,
                    node_id="publish",
                ),
            )
            with pytest.raises(ValueError, match="does not match the connector"):
                await WorkflowInterruptionRepository(session).create_capability_approval(
                    str(queued.run_id),
                    "checkpoint-invalid-resource",
                    CapabilityApprovalRequest(
                        authorization=request,
                        arguments_fingerprint=fingerprint_capability_arguments({"value": "report"}),
                    ),
                )
    finally:
        await database.close()
