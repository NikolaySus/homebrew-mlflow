"""Retain the Principal that requested each publication."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_publication_actor"
down_revision: str | None = "0018_gitlab_memberships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication_operations",
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("principals.id")),
    )


def downgrade() -> None:
    op.drop_column("publication_operations", "created_by")
