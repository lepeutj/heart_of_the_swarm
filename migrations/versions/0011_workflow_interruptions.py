"""Add durable workflow interruptions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_workflow_interruptions"
down_revision: str | Sequence[str] | None = "0010_execution_threads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_interruptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("prompt", sa.String(length=1000), nullable=False),
        sa.Column("response_schema", sa.JSON(), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('approval')",
            name="ck_workflow_interruptions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'cancelled')",
            name="ck_workflow_interruptions_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["execution_threads.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "node_id",
            "checkpoint_id",
            name="uq_workflow_interruption_location",
        ),
    )
    op.create_index(
        "ix_workflow_interruptions_workflow_version_id",
        "workflow_interruptions",
        ["workflow_version_id"],
    )
    op.create_index(
        "ix_workflow_interruptions_thread_id",
        "workflow_interruptions",
        ["thread_id"],
    )
    op.create_index(
        "ix_workflow_interruptions_workflow_run_id",
        "workflow_interruptions",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_interruptions_status",
        "workflow_interruptions",
        ["status"],
    )
    op.create_index(
        "ix_workflow_interruptions_created_at",
        "workflow_interruptions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_interruptions_created_at", table_name="workflow_interruptions")
    op.drop_index("ix_workflow_interruptions_status", table_name="workflow_interruptions")
    op.drop_index("ix_workflow_interruptions_workflow_run_id", table_name="workflow_interruptions")
    op.drop_index("ix_workflow_interruptions_thread_id", table_name="workflow_interruptions")
    op.drop_index(
        "ix_workflow_interruptions_workflow_version_id",
        table_name="workflow_interruptions",
    )
    op.drop_table("workflow_interruptions")
