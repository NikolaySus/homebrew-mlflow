"""Persist verified GitLab email for provider membership reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_gitlab_identity_email"
down_revision: str | None = "0023_automated_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gitlab_identity_bindings",
        sa.Column("email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gitlab_identity_bindings", "email")
