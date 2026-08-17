"""Add policy-limited Run attachment metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_run_attachments"
down_revision: str | None = "0009_run_tracking_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "path", name="uq_run_attachment_path"),
        sa.CheckConstraint("size >= 0", name="ck_run_attachment_size_nonnegative"),
    )
    op.create_index("ix_run_attachment_run", "run_attachments", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_attachment_run", table_name="run_attachments")
    op.drop_table("run_attachments")
