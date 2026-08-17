"""Retain attachment history after policy-driven byte purge."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_retention_markers"
down_revision: str | None = "0015_secret_contexts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_attachments", sa.Column("purged_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("run_attachments", "purged_at")
