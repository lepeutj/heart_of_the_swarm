"""Add observable agent trajectory steps."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_trajectory_steps"
down_revision: str | Sequence[str] | None = "0003_run_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trajectory_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("component", sa.String(length=200), nullable=True),
        sa.Column("langchain_run_id", sa.String(length=36), nullable=True),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt", "sequence"),
    )
    op.create_index("ix_trajectory_steps_run_id", "trajectory_steps", ["run_id"])
    op.create_index("ix_trajectory_steps_event_type", "trajectory_steps", ["event_type"])
    op.create_index("ix_trajectory_steps_created_at", "trajectory_steps", ["created_at"])


def downgrade() -> None:
    op.drop_table("trajectory_steps")
