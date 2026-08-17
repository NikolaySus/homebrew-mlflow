"""Add immutable GitLab identity bindings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_gitlab_identity_bindings"
down_revision: str | None = "0004_git_repositories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gitlab_identity_bindings",
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), primary_key=True),
        sa.Column("subject", sa.String(200), nullable=False, unique=True),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gitlab_identity_bindings")
