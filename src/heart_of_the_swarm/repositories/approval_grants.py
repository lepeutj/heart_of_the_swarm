from datetime import UTC, datetime

from sqlalchemy import update

from heart_of_the_swarm.approval_grants import ApprovalGrant, ApprovalGrantStatus
from heart_of_the_swarm.authorization import AuthorizationRequest, Principal
from heart_of_the_swarm.models import ApprovalGrantRecord
from heart_of_the_swarm.repositories.base import RepositoryBase


class ApprovalGrantRepository(RepositoryBase):
    """Read and atomically consume exact request-bound approval grants."""

    async def get(self, grant_id: str) -> ApprovalGrant | None:
        record = await self.session.get(ApprovalGrantRecord, grant_id)
        return self._detail(record) if record else None

    async def consume(
        self,
        grant_id: str,
        request: AuthorizationRequest,
        arguments_fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> ApprovalGrant:
        """Consume one exact active grant before its protected side effect is released."""
        checked_at = now or datetime.now(UTC)
        context = request.context
        if context.run_id is None or context.node_id is None:
            raise ValueError("approval grant consumption requires run and node context")

        statement = (
            update(ApprovalGrantRecord)
            .where(
                ApprovalGrantRecord.id == grant_id,
                ApprovalGrantRecord.continuation_run_id == str(context.run_id),
                ApprovalGrantRecord.node_id == context.node_id,
                ApprovalGrantRecord.principal_kind == request.principal.kind,
                ApprovalGrantRecord.principal_id == request.principal.id,
                ApprovalGrantRecord.action == request.action,
                ApprovalGrantRecord.resource == request.resource,
                ApprovalGrantRecord.arguments_fingerprint == arguments_fingerprint,
                ApprovalGrantRecord.status == ApprovalGrantStatus.ACTIVE,
                ApprovalGrantRecord.expires_at > checked_at,
                ApprovalGrantRecord.consumed_at.is_(None),
                ApprovalGrantRecord.consumed_by_run_id.is_(None),
            )
            .values(
                status=ApprovalGrantStatus.CONSUMED,
                consumed_by_run_id=str(context.run_id),
                consumed_at=checked_at,
            )
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("approval grant is not active for this request")
        await self.session.commit()
        return self._detail(await self._required(ApprovalGrantRecord, grant_id))

    @staticmethod
    def _detail(record: ApprovalGrantRecord) -> ApprovalGrant:
        return ApprovalGrant(
            id=record.id,
            interruption_id=record.interruption_id,
            source_run_id=record.source_run_id,
            continuation_run_id=record.continuation_run_id,
            node_id=record.node_id,
            principal=Principal(kind=record.principal_kind, id=record.principal_id),
            action=record.action,
            resource=record.resource,
            arguments_fingerprint=record.arguments_fingerprint,
            approved_by=Principal(
                kind=record.approved_by_kind,
                id=record.approved_by_id,
            ),
            status=record.status,
            created_at=_aware(record.created_at),
            expires_at=_aware(record.expires_at),
            consumed_by_run_id=record.consumed_by_run_id,
            consumed_at=_aware(record.consumed_at) if record.consumed_at else None,
        )


def _aware(value: datetime) -> datetime:
    """Restore UTC for SQLite values that lose timezone metadata."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
