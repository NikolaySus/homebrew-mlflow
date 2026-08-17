"""Enforce the 90-day machine bootstrap credential lifetime."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_machine_credential_expiry"
down_revision: str | None = "0021_environment_specs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "machine_credentials",
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        "UPDATE machine_credentials "
        "SET expires_at = created_at + INTERVAL '90 days' "
        "WHERE expires_at IS NULL"
    )
    op.alter_column("machine_credentials", "expires_at", nullable=False)


def downgrade() -> None:
    op.drop_column("machine_credentials", "expires_at")
