"""Add canonical Experiments and Runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_experiments_runs"
down_revision: str | None = "0007_project_provisioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_experiment_project_name"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("repository_id", sa.Uuid(), sa.ForeignKey("git_repositories.id"), nullable=False),
        sa.Column(
            "creator_principal_id",
            sa.Uuid(),
            sa.ForeignKey("principals.id"),
            nullable=False,
        ),
        sa.Column("retry_of_run_id", sa.Uuid(), sa.ForeignKey("runs.id")),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("finalization_digest", sa.String(64)),
        sa.Column("git_commit_sha", sa.String(64)),
        sa.CheckConstraint(
            "state IN ('created', 'running', 'finalizing', 'succeeded', 'failed', "
            "'interrupted', 'incomplete')",
            name="ck_run_state",
        ),
    )
    op.create_index("ix_run_heartbeat", "runs", ["state", "heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_run_heartbeat", table_name="runs")
    op.drop_table("runs")
    op.drop_table("experiments")
