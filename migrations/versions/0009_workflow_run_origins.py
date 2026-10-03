"""Record trigger origin on durable workflow runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_workflow_run_origins"
down_revision: str | Sequence[str] | None = "0008_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("trigger_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("trigger_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("trigger_event_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_workflow_runs_trigger_id",
            "triggers",
            ["trigger_id"],
            ["id"],
        )
        batch.create_index("ix_workflow_runs_trigger_id", ["trigger_id"])
        batch.create_index("ix_workflow_runs_trigger_event_id", ["trigger_event_id"])


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_index("ix_workflow_runs_trigger_event_id")
        batch.drop_index("ix_workflow_runs_trigger_id")
        batch.drop_constraint("fk_workflow_runs_trigger_id", type_="foreignkey")
        batch.drop_column("trigger_event_id")
        batch.drop_column("trigger_type")
        batch.drop_column("trigger_id")
