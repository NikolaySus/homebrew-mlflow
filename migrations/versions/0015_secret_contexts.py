"""Store non-secret Infisical routing and reconciliation state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_secret_contexts"
down_revision: str | None = "0014_run_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secret_contexts",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            primary_key=True,
        ),
        sa.Column("infisical_project_id", sa.String(200), nullable=False),
        sa.Column("environment_slug", sa.String(100), nullable=False),
        sa.Column("secret_path", sa.String(1000), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciliation_state", sa.String(24), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("reconcile_attempt", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("secret_contexts")
