"""Add execution threads and resume references to workflow runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_execution_threads"
down_revision: str | Sequence[str] | None = "0009_workflow_run_origins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_threads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_threads_workflow_version_id",
        "execution_threads",
        ["workflow_version_id"],
    )
    op.create_index("ix_execution_threads_status", "execution_threads", ["status"])

    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("thread_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("attempt_index", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("resumed_from_run_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("resume_checkpoint_id", sa.String(length=100), nullable=True))
        batch.create_foreign_key(
            "fk_workflow_runs_thread_id",
            "execution_threads",
            ["thread_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_workflow_runs_resumed_from_run_id",
            "workflow_runs",
            ["resumed_from_run_id"],
            ["id"],
        )
        batch.create_index("ix_workflow_runs_thread_id", ["thread_id"])
        batch.create_unique_constraint(
            "uq_workflow_runs_thread_attempt",
            ["thread_id", "attempt_index"],
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_constraint("uq_workflow_runs_thread_attempt", type_="unique")
        batch.drop_index("ix_workflow_runs_thread_id")
        batch.drop_constraint("fk_workflow_runs_resumed_from_run_id", type_="foreignkey")
        batch.drop_constraint("fk_workflow_runs_thread_id", type_="foreignkey")
        batch.drop_column("resume_checkpoint_id")
        batch.drop_column("resumed_from_run_id")
        batch.drop_column("attempt_index")
        batch.drop_column("thread_id")

    op.drop_index("ix_execution_threads_status", table_name="execution_threads")
    op.drop_index(
        "ix_execution_threads_workflow_version_id",
        table_name="execution_threads",
    )
    op.drop_table("execution_threads")
