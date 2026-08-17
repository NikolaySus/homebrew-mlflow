"""Track asynchronous GitLab membership reconciliation per project."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_gitlab_memberships"
down_revision: str | None = "0017_machine_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column(
            "gitlab_reconciliation_state",
            sa.String(24),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "research_projects", sa.Column("gitlab_reconciliation_error", sa.String(100))
    )
    op.add_column(
        "research_projects", sa.Column("gitlab_reconciled_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "research_projects",
        sa.Column("gitlab_reconcile_attempt", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("research_projects", "gitlab_reconcile_attempt")
    op.drop_column("research_projects", "gitlab_reconciled_at")
    op.drop_column("research_projects", "gitlab_reconciliation_error")
    op.drop_column("research_projects", "gitlab_reconciliation_state")
