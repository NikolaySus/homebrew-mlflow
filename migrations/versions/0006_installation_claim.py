"""Add one-time installation claim state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_installation_claim"
down_revision: str | None = "0005_gitlab_identity_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installation_claim",
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton", name="ck_installation_claim_singleton"),
    )


def downgrade() -> None:
    op.drop_table("installation_claim")
