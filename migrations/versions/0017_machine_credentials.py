"""Add scoped machine-principal credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_machine_credentials"
down_revision: str | None = "0016_retention_markers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "machine_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("principal_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column(
            "project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False
        ),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("machine_credentials")
