"""Add public credential references to MCP server definitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_mcp_credential_refs"
down_revision: str | Sequence[str] | None = "0011_workflow_interruptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("bearer_credential_ref", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "bearer_credential_ref")
