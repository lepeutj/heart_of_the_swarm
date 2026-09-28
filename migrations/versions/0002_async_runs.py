"""Add prompt lineage and durable asynchronous runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_async_runs"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch:
        batch.add_column(sa.Column("prompt_version", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("system_prompt", sa.Text(), nullable=True))
    op.execute("UPDATE agent_versions SET prompt_version = '1' WHERE prompt_version IS NULL")
    op.execute("UPDATE agent_versions SET system_prompt = '' WHERE system_prompt IS NULL")
    with op.batch_alter_table("agent_versions") as batch:
        batch.alter_column("prompt_version", nullable=False)
        batch.alter_column("system_prompt", nullable=False)

    op.create_table(
        "design_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("builder_provider", sa.String(length=40), nullable=False),
        sa.Column("builder_model_id", sa.String(length=200), nullable=False),
        sa.Column("builder_prompt", sa.Text(), nullable=False),
        sa.Column("generated_spec", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_sessions_trace_id", "design_sessions", ["trace_id"])

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("attempt", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("worker_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.execute("UPDATE runs SET queued_at = started_at WHERE queued_at IS NULL")
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("queued_at", nullable=False)
    op.create_index("ix_runs_queued_at", "runs", ["queued_at"])
    op.create_index("ix_runs_status", "runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_queued_at", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.drop_column("cancel_requested_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("queued_at")
        batch.drop_column("worker_id")
        batch.drop_column("attempt")
    op.drop_table("design_sessions")
    with op.batch_alter_table("agent_versions") as batch:
        batch.drop_column("system_prompt")
        batch.drop_column("prompt_version")
