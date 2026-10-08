"""Persist request-bound capability approval grants."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_approval_grants"
down_revision: str | Sequence[str] | None = "0012_mcp_credential_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_interruptions") as batch:
        batch.add_column(sa.Column("request", sa.JSON(), nullable=True))
        batch.drop_constraint("ck_workflow_interruptions_kind", type_="check")
        batch.create_check_constraint(
            "ck_workflow_interruptions_kind",
            "kind IN ('approval', 'capability_approval')",
        )

    op.create_table(
        "approval_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("interruption_id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("continuation_run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("principal_kind", sa.String(length=30), nullable=False),
        sa.Column("principal_id", sa.String(length=300), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=320), nullable=False),
        sa.Column("arguments_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("approved_by_kind", sa.String(length=30), nullable=False),
        sa.Column("approved_by_id", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by_run_id", sa.String(length=36), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'consumed')",
            name="ck_approval_grants_status",
        ),
        sa.ForeignKeyConstraint(["interruption_id"], ["workflow_interruptions.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["continuation_run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["consumed_by_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interruption_id"),
    )
    op.create_index("ix_approval_grants_interruption_id", "approval_grants", ["interruption_id"])
    op.create_index("ix_approval_grants_source_run_id", "approval_grants", ["source_run_id"])
    op.create_index(
        "ix_approval_grants_continuation_run_id",
        "approval_grants",
        ["continuation_run_id"],
    )
    op.create_index("ix_approval_grants_status", "approval_grants", ["status"])
    op.create_index("ix_approval_grants_expires_at", "approval_grants", ["expires_at"])


def downgrade() -> None:
    op.drop_table("approval_grants")
    with op.batch_alter_table("workflow_interruptions") as batch:
        batch.drop_constraint("ck_workflow_interruptions_kind", type_="check")
        batch.create_check_constraint(
            "ck_workflow_interruptions_kind",
            "kind IN ('approval')",
        )
        batch.drop_column("request")
