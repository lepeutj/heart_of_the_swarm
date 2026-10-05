"""Persist typed triggers targeting immutable product versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_triggers"
down_revision: str | Sequence[str] | None = "0007_workflow_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_version_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trigger_type IN ('manual', 'webhook', 'schedule')",
            name="ck_triggers_type",
        ),
        sa.CheckConstraint(
            "target_type IN ('agent', 'workflow')",
            name="ck_triggers_target_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_triggers_trigger_type", "triggers", ["trigger_type"])
    op.create_index("ix_triggers_target_type", "triggers", ["target_type"])
    op.create_index("ix_triggers_target_version_id", "triggers", ["target_version_id"])
    op.create_index("ix_triggers_enabled", "triggers", ["enabled"])
    op.create_index("ix_triggers_next_fire_at", "triggers", ["next_fire_at"])


def downgrade() -> None:
    op.drop_index("ix_triggers_next_fire_at", table_name="triggers")
    op.drop_index("ix_triggers_enabled", table_name="triggers")
    op.drop_index("ix_triggers_target_version_id", table_name="triggers")
    op.drop_index("ix_triggers_target_type", table_name="triggers")
    op.drop_index("ix_triggers_trigger_type", table_name="triggers")
    op.drop_table("triggers")
