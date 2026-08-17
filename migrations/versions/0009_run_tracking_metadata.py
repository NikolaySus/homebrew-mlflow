"""Add canonical Run parameters, metric histories, and tags."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_run_tracking_metadata"
down_revision: str | None = "0008_experiments_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_parameters",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("key", sa.String(250), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "run_metrics",
        sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("key", sa.String(250), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("step", sa.BigInteger(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("timestamp_ms >= 0", name="ck_metric_timestamp_nonnegative"),
        sa.CheckConstraint("step >= 0", name="ck_metric_step_nonnegative"),
    )
    op.create_index(
        "ix_run_metric_history", "run_metrics", ["run_id", "key", "step", "timestamp_ms"]
    )
    op.create_table(
        "run_tags",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("key", sa.String(250), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_tags")
    op.drop_index("ix_run_metric_history", table_name="run_metrics")
    op.drop_table("run_metrics")
    op.drop_table("run_parameters")
