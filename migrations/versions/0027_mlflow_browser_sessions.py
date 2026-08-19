"""Add revocable browser sessions for the MLflow gateway."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_mlflow_browser_sessions"
down_revision: str | None = "0026_run_finalization_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mlflow_browser_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("default_project_id", sa.Uuid(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["default_project_id"], ["research_projects.id"]),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
    )
    op.create_index(
        op.f("ix_mlflow_browser_sessions_principal_id"),
        "mlflow_browser_sessions",
        ["principal_id"],
    )
    op.create_index(
        op.f("ix_mlflow_browser_sessions_default_project_id"),
        "mlflow_browser_sessions",
        ["default_project_id"],
    )
    op.create_index(
        op.f("ix_mlflow_browser_sessions_expires_at"),
        "mlflow_browser_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_mlflow_browser_sessions_expires_at"),
        table_name="mlflow_browser_sessions",
    )
    op.drop_index(
        op.f("ix_mlflow_browser_sessions_default_project_id"),
        table_name="mlflow_browser_sessions",
    )
    op.drop_index(
        op.f("ix_mlflow_browser_sessions_principal_id"),
        table_name="mlflow_browser_sessions",
    )
    op.drop_table("mlflow_browser_sessions")
