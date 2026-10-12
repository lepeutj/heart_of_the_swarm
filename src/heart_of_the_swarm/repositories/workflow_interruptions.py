from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from jsonschema.validators import validator_for
from sqlalchemy import select, update

from heart_of_the_swarm.approval_grants import (
    ApprovalGrantStatus,
    CapabilityApprovalRequest,
)
from heart_of_the_swarm.authorization import Principal, PrincipalKind
from heart_of_the_swarm.models import (
    ApprovalGrantRecord,
    ExecutionThreadRecord,
    WorkflowInterruptionRecord,
    WorkflowRunEventRecord,
    WorkflowRunRecord,
)
from heart_of_the_swarm.repositories.agents import AgentRepository
from heart_of_the_swarm.repositories.base import RepositoryBase
from heart_of_the_swarm.repositories.workflow_runs import WorkflowRunRepository
from heart_of_the_swarm.repositories.workflows import WorkflowRepository
from heart_of_the_swarm.spec import RunStatus
from heart_of_the_swarm.workflows.configs import (
    AgentNodeConfig,
    ConnectorNodeConfig,
    HumanApprovalNodeConfig,
    InlineAgentSource,
    SupervisorNodeConfig,
    VersionedAgentSource,
)
from heart_of_the_swarm.workflows.enums import NodeType
from heart_of_the_swarm.workflows.interruptions import (
    APPROVAL_RESPONSE_SCHEMA,
    WorkflowInterruptionDetail,
    WorkflowInterruptionStatus,
)
from heart_of_the_swarm.workflows.runs import ExecutionThreadStatus, WorkflowRunAccepted


class WorkflowInterruptionRepository(RepositoryBase):
    """Persist and atomically resolve durable human workflow interruptions."""

    async def create_approval(
        self,
        run_id: str,
        node_id: str,
        checkpoint_id: str,
    ) -> WorkflowInterruptionDetail:
        """Atomically interrupt one running workflow with a durable approval request."""
        run = (
            await self.session.execute(
                select(WorkflowRunRecord).where(WorkflowRunRecord.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            raise ValueError("workflow_runs record not found")
        if run.status != RunStatus.RUNNING or run.thread_id is None:
            raise ValueError("workflow run is not running in a durable thread")

        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        if version is None:
            raise ValueError("workflow version not found")
        node = next((item for item in version.spec.nodes if item.id == node_id), None)
        if node is None or node.type != NodeType.HUMAN_APPROVAL:
            raise ValueError("workflow node is not a human approval")
        config = HumanApprovalNodeConfig.model_validate(node.config)

        existing = (
            await self.session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == run.id,
                    WorkflowInterruptionRecord.node_id == node_id,
                    WorkflowInterruptionRecord.checkpoint_id == checkpoint_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("workflow interruption already exists")

        record = WorkflowInterruptionRecord(
            id=str(uuid4()),
            workflow_version_id=run.workflow_version_id,
            thread_id=run.thread_id,
            workflow_run_id=run.id,
            node_id=node_id,
            kind="approval",
            prompt=config.prompt,
            response_schema=APPROVAL_RESPONSE_SCHEMA,
            checkpoint_id=checkpoint_id,
            status=WorkflowInterruptionStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        now = datetime.now(UTC)
        run.status = RunStatus.INTERRUPTED
        run.resume_checkpoint_id = checkpoint_id
        run.completed_at = now
        run.lease_expires_at = None
        thread = await self._required(ExecutionThreadRecord, run.thread_id)
        thread.status = ExecutionThreadStatus.INTERRUPTED
        thread.updated_at = now
        self.session.add(record)
        self._add_event(
            run,
            "approval.requested",
            {
                "interruption_id": record.id,
                "node_id": node_id,
                "checkpoint_id": checkpoint_id,
            },
        )
        self._add_event(
            run,
            "workflow.interrupted",
            {"checkpoint_id": checkpoint_id, "node_id": node_id},
        )
        await self.session.commit()
        return self._detail(record)

    async def create_capability_approval(
        self,
        run_id: str,
        checkpoint_id: str,
        request: CapabilityApprovalRequest,
    ) -> WorkflowInterruptionDetail:
        """Persist one policy-driven capability interruption before any external effect."""
        run = await self._required(WorkflowRunRecord, run_id)
        context = request.authorization.context
        if run.status != RunStatus.RUNNING or run.thread_id is None:
            raise ValueError("workflow run is not running in a durable thread")
        if str(context.run_id) != run.id:
            raise ValueError("capability approval does not match the workflow run")
        if str(context.workflow_version_id) != run.workflow_version_id:
            raise ValueError("capability approval does not match the workflow version")
        version = await WorkflowRepository(self.session).get_version(run.workflow_version_id)
        node = (
            next(
                (item for item in version.spec.nodes if item.id == context.node_id),
                None,
            )
            if version
            else None
        )
        if node is None:
            raise ValueError("capability approval workflow node was not found")
        if node.type == NodeType.CONNECTOR:
            connector = ConnectorNodeConfig.model_validate(node.config)
            capability_ids = {connector.capability_id}
            expected_principal = Principal(
                kind=PrincipalKind.WORKFLOW_NODE,
                id=f"{run.workflow_version_id}/{node.id}",
            )
        elif node.type in {NodeType.AGENT, NodeType.SUPERVISOR}:
            config = (
                AgentNodeConfig.model_validate(node.config)
                if node.type == NodeType.AGENT
                else SupervisorNodeConfig.model_validate(node.config)
            )
            source = config.source
            if isinstance(source, InlineAgentSource):
                capability_ids = set(source.agent.tools)
                expected_principal = Principal(
                    kind=PrincipalKind.WORKFLOW_NODE,
                    id=f"{run.workflow_version_id}/{node.id}",
                )
            elif isinstance(source, VersionedAgentSource):
                agent = await AgentRepository(self.session).get_version(
                    str(source.agent_version_id)
                )
                if agent is None:
                    raise ValueError("capability approval agent version was not found")
                capability_ids = set(agent.spec.tools)
                expected_principal = Principal(
                    kind=PrincipalKind.AGENT_VERSION,
                    id=str(source.agent_version_id),
                )
            else:
                raise TypeError("capability approval agent source is invalid")
        else:
            raise ValueError("capability approval node cannot invoke capabilities")
        capability_id = request.authorization.resource.removeprefix("capability:")
        if capability_id not in capability_ids:
            if node.type == NodeType.CONNECTOR:
                raise ValueError("capability approval resource does not match the connector")
            raise ValueError("capability approval resource is not declared by the agent")
        if request.authorization.principal != expected_principal:
            raise ValueError("capability approval principal does not match the workflow node")

        existing = (
            await self.session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == run.id,
                    WorkflowInterruptionRecord.node_id == node.id,
                    WorkflowInterruptionRecord.checkpoint_id == checkpoint_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("workflow interruption already exists")

        record = WorkflowInterruptionRecord(
            id=str(uuid4()),
            workflow_version_id=run.workflow_version_id,
            thread_id=run.thread_id,
            workflow_run_id=run.id,
            node_id=node.id,
            kind="capability_approval",
            prompt=f"Allow {request.authorization.resource} for this request?",
            response_schema=APPROVAL_RESPONSE_SCHEMA,
            request=request.model_dump(mode="json"),
            checkpoint_id=checkpoint_id,
            status=WorkflowInterruptionStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        now = datetime.now(UTC)
        thread = await self._required(ExecutionThreadRecord, run.thread_id)
        thread.status = ExecutionThreadStatus.INTERRUPTED
        thread.updated_at = now
        self._interrupt_run(run, checkpoint_id, node.id, record)
        await self.session.commit()
        return self._detail(record)

    async def get(self, interruption_id: str) -> WorkflowInterruptionDetail | None:
        record = await self.session.get(WorkflowInterruptionRecord, interruption_id)
        return self._detail(record) if record else None

    async def list(
        self,
        status: WorkflowInterruptionStatus | None = None,
    ) -> list[WorkflowInterruptionDetail]:
        """List human interruptions in newest-first product order."""
        statement = select(WorkflowInterruptionRecord)
        if status is not None:
            statement = statement.where(WorkflowInterruptionRecord.status == status)
        records = (
            await self.session.execute(
                statement.order_by(WorkflowInterruptionRecord.created_at.desc())
            )
        ).scalars()
        return [self._detail(record) for record in records]

    async def pending_for_run(self, run_id: str) -> WorkflowInterruptionDetail | None:
        """Return the pending human interruption that owns a run's next resume."""
        record = (
            await self.session.execute(
                select(WorkflowInterruptionRecord).where(
                    WorkflowInterruptionRecord.workflow_run_id == run_id,
                    WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
                )
            )
        ).scalar_one_or_none()
        return self._detail(record) if record else None

    async def resolve_and_resume(
        self,
        interruption_id: str,
        *,
        response: dict[str, Any],
        trace_id: str,
        approved_by: Principal | None = None,
        grant_ttl_seconds: int = 300,
    ) -> WorkflowRunAccepted:
        """Atomically resolve one approval and create its queued continuation run."""
        record = await self._required(WorkflowInterruptionRecord, interruption_id)
        if record.status != WorkflowInterruptionStatus.PENDING:
            raise ValueError("workflow interruption is not pending")
        self._validate_response(record.response_schema, response)
        now = datetime.now(UTC)
        previous = await self._required(WorkflowRunRecord, record.workflow_run_id)
        claim = (
            update(WorkflowInterruptionRecord)
            .where(
                WorkflowInterruptionRecord.id == interruption_id,
                WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
            )
            .values(
                status=WorkflowInterruptionStatus.RESOLVED,
                response=response,
                resolved_at=now,
            )
        )
        claimed = await self.session.execute(claim)
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("workflow interruption is not pending")
        accepted = await WorkflowRunRepository(self.session).resume(
            record.workflow_run_id,
            record.checkpoint_id,
            trace_id,
            commit=False,
        )
        stored_response = dict(response)
        grant: ApprovalGrantRecord | None = None
        if record.kind == "capability_approval" and response["approved"]:
            if approved_by is None or approved_by.kind != PrincipalKind.USER:
                await self.session.rollback()
                raise ValueError("capability approval requires an authenticated user approver")
            if grant_ttl_seconds <= 0:
                await self.session.rollback()
                raise ValueError("approval grant lifetime must be positive")
            pending = CapabilityApprovalRequest.model_validate(record.request)
            grant_id = str(uuid4())
            stored_response["grant_id"] = grant_id
            authorization = pending.authorization
            grant = ApprovalGrantRecord(
                id=grant_id,
                interruption_id=record.id,
                source_run_id=record.workflow_run_id,
                continuation_run_id=str(accepted.run_id),
                node_id=record.node_id,
                principal_kind=authorization.principal.kind,
                principal_id=authorization.principal.id,
                action=authorization.action,
                resource=authorization.resource,
                arguments_fingerprint=pending.arguments_fingerprint,
                approved_by_kind=approved_by.kind,
                approved_by_id=approved_by.id,
                status=ApprovalGrantStatus.ACTIVE,
                created_at=now,
                expires_at=now + timedelta(seconds=grant_ttl_seconds),
            )
        if stored_response != response:
            await self.session.execute(
                update(WorkflowInterruptionRecord)
                .where(WorkflowInterruptionRecord.id == interruption_id)
                .values(response=stored_response)
            )
        if grant is not None:
            self.session.add(grant)
        self._add_event(
            previous,
            "approval.resolved",
            {
                "interruption_id": record.id,
                "node_id": record.node_id,
                "approved": response["approved"],
            },
        )
        await self.session.commit()
        return accepted

    async def cancel(
        self,
        interruption_id: str,
        *,
        thread_id: str,
        checkpoint_id: str,
    ) -> WorkflowInterruptionDetail:
        """Atomically cancel one pending interruption without creating a resumed run."""
        record = await self._required(WorkflowInterruptionRecord, interruption_id)
        self._validate_context(record, thread_id, checkpoint_id)
        statement = (
            update(WorkflowInterruptionRecord)
            .where(
                WorkflowInterruptionRecord.id == interruption_id,
                WorkflowInterruptionRecord.thread_id == thread_id,
                WorkflowInterruptionRecord.checkpoint_id == checkpoint_id,
                WorkflowInterruptionRecord.status == WorkflowInterruptionStatus.PENDING,
            )
            .values(
                status=WorkflowInterruptionStatus.CANCELLED,
                resolved_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("workflow interruption is not pending")
        await self.session.commit()
        cancelled = await self._required(WorkflowInterruptionRecord, interruption_id)
        return self._detail(cancelled)

    @staticmethod
    def _validate_context(
        record: WorkflowInterruptionRecord,
        thread_id: str,
        checkpoint_id: str,
    ) -> None:
        if record.thread_id != thread_id or record.checkpoint_id != checkpoint_id:
            raise ValueError("workflow interruption does not match the execution context")
        if record.status != WorkflowInterruptionStatus.PENDING:
            raise ValueError("workflow interruption is not pending")

    @staticmethod
    def _validate_response(schema: dict[str, Any], response: dict[str, Any]) -> None:
        validator = validator_for(schema)(schema)
        errors = sorted(validator.iter_errors(response), key=lambda error: list(error.path))
        if errors:
            raise ValueError(f"workflow interruption response is invalid: {errors[0].message}")

    def _add_event(
        self,
        run: WorkflowRunRecord,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Append an event in the same transaction as the interruption lifecycle change."""
        run.event_sequence += 1
        self.session.add(
            WorkflowRunEventRecord(
                id=str(uuid4()),
                workflow_run_id=run.id,
                sequence=run.event_sequence,
                event_type=event_type,
                data=data,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _detail(record: WorkflowInterruptionRecord) -> WorkflowInterruptionDetail:
        return WorkflowInterruptionDetail(
            id=record.id,
            workflow_version_id=record.workflow_version_id,
            thread_id=record.thread_id,
            workflow_run_id=record.workflow_run_id,
            node_id=record.node_id,
            kind=record.kind,
            prompt=record.prompt,
            response_schema=record.response_schema,
            request=record.request,
            checkpoint_id=record.checkpoint_id,
            status=record.status,
            response=record.response,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
        )

    def _interrupt_run(
        self,
        run: WorkflowRunRecord,
        checkpoint_id: str,
        node_id: str,
        record: WorkflowInterruptionRecord,
    ) -> None:
        """Apply the durable lifecycle transition for a capability interruption."""
        now = datetime.now(UTC)
        run.status = RunStatus.INTERRUPTED
        run.resume_checkpoint_id = checkpoint_id
        run.completed_at = now
        run.lease_expires_at = None
        self.session.add(record)
        self._add_event(
            run,
            "approval.requested",
            {
                "interruption_id": record.id,
                "node_id": node_id,
                "checkpoint_id": checkpoint_id,
                "kind": record.kind,
            },
        )
        self._add_event(
            run,
            "workflow.interrupted",
            {"checkpoint_id": checkpoint_id, "node_id": node_id},
        )
