"""Persist Run finalization idempotency metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_run_finalization_recovery"
down_revision: str | None = "0025_run_provenance_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("finalization_idempotency_key", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "finalization_idempotency_key")
