"""Add rotating human refresh credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_refresh_credentials"
down_revision: str | None = "0001_identity_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "human_refresh_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("family_id", "sequence", name="uq_refresh_family_sequence"),
    )
    op.create_index("ix_refresh_credentials_family", "human_refresh_credentials", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_credentials_family", table_name="human_refresh_credentials")
    op.drop_table("human_refresh_credentials")
