"""Add disposable full-ZIP Artifact download jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_artifact_download_archives"
down_revision: str | None = "0031_progress_display_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_download_archives",
        sa.Column("artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("processed_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("archive_size", sa.BigInteger(), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'building', 'ready', 'failed', 'expired')",
            name="ck_artifact_download_archives_state",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_version_id"],
            ["artifact_versions.id"],
        ),
        sa.PrimaryKeyConstraint("artifact_version_id"),
    )
    op.create_index(
        "ix_artifact_download_archives_state",
        "artifact_download_archives",
        ["state"],
    )
    op.create_index(
        "ix_artifact_download_archives_updated_at",
        "artifact_download_archives",
        ["updated_at"],
    )
    op.create_index(
        "ix_artifact_download_archives_expires_at",
        "artifact_download_archives",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_download_archives_expires_at",
        table_name="artifact_download_archives",
    )
    op.drop_index(
        "ix_artifact_download_archives_updated_at",
        table_name="artifact_download_archives",
    )
    op.drop_index(
        "ix_artifact_download_archives_state",
        table_name="artifact_download_archives",
    )
    op.drop_table("artifact_download_archives")
