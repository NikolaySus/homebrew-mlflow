"""Add durable publication operation queue and events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_publication_queue"
down_revision: str | None = "0002_refresh_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "publication_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(100)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_publication_idempotency"),
    )
    op.create_index("ix_publication_queue", "publication_operations", ["state", "created_at"])
    op.create_table(
        "publication_events",
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("publication_operations.id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_table("publication_events")
    op.drop_index("ix_publication_queue", table_name="publication_operations")
    op.drop_table("publication_operations")
