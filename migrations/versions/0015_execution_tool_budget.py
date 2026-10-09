"""Persist thread-wide tool-call consumption."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_execution_tool_budget"
down_revision: str | Sequence[str] | None = "0014_execution_model_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("execution_threads") as batch:
        batch.add_column(sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("execution_threads") as batch:
        batch.drop_column("tool_calls")
