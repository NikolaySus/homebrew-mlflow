"""Add immutable Artifact catalog and publication results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_artifact_catalog"
down_revision: str | None = "0010_run_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "owning_project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owning_project_id", "name", name="uq_artifact_project_name"),
    )
    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column(
            "owning_project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False
        ),
        sa.Column(
            "publication_operation_id",
            sa.Uuid(),
            sa.ForeignKey("publication_operations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("producing_run_id", sa.Uuid(), sa.ForeignKey("runs.id")),
        sa.Column("algorithm", sa.String(16), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("output_kind", sa.String(16), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.BigInteger(), nullable=False),
        sa.Column("integrity", sa.String(16), nullable=False),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "artifact_version_files",
        sa.Column(
            "artifact_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            primary_key=True,
        ),
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(64)),
    )
    op.create_table(
        "artifact_storage_locations",
        sa.Column(
            "artifact_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            primary_key=True,
        ),
        sa.Column("bucket", sa.String(200), primary_key=True),
        sa.Column("object_key", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "publication_operations",
        sa.Column("artifact_version_id", sa.Uuid(), sa.ForeignKey("artifact_versions.id")),
    )
    op.add_column("publication_operations", sa.Column("failure_code", sa.String(100)))


def downgrade() -> None:
    op.drop_column("publication_operations", "failure_code")
    op.drop_column("publication_operations", "artifact_version_id")
    op.drop_table("artifact_storage_locations")
    op.drop_table("artifact_version_files")
    op.drop_table("artifact_versions")
    op.drop_table("artifacts")
