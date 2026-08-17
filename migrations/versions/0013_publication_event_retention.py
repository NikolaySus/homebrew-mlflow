"""Track the publication event replay boundary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_publication_event_retention"
down_revision: str | None = "0012_artifact_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication_operations",
        sa.Column("events_expired_through", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("publication_operations", "events_expired_through")
