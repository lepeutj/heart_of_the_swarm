"""Add durable workflow runs and business events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workflow_runs"
down_revision: str | Sequence[str] | None = "0006_mcp_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_runs_workflow_version_id",
        "workflow_runs",
        ["workflow_version_id"],
    )
    op.create_index("ix_workflow_runs_trace_id", "workflow_runs", ["trace_id"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_queued_at", "workflow_runs", ["queued_at"])

    op.create_table(
        "workflow_run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "sequence"),
    )
    op.create_index(
        "ix_workflow_run_events_workflow_run_id",
        "workflow_run_events",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_run_events_event_type",
        "workflow_run_events",
        ["event_type"],
    )
    op.create_index(
        "ix_workflow_run_events_created_at",
        "workflow_run_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_run_events_created_at", table_name="workflow_run_events")
    op.drop_index("ix_workflow_run_events_event_type", table_name="workflow_run_events")
    op.drop_index("ix_workflow_run_events_workflow_run_id", table_name="workflow_run_events")
    op.drop_table("workflow_run_events")
    op.drop_index("ix_workflow_runs_queued_at", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_trace_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_version_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
