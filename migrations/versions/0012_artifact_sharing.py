"""Add exact-version sharing, references, and derivation lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_artifact_sharing"
down_revision: str | None = "0011_artifact_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_sharing_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "artifact_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "owning_project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "consuming_project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_sharing_grant_lookup",
        "artifact_sharing_grants",
        ["artifact_version_id", "consuming_project_id", "created_at"],
    )
    op.create_table(
        "shared_artifact_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "artifact_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_sharing_grants.id"),
            nullable=False,
        ),
        sa.Column(
            "consuming_project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "artifact_derivations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "source_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "derived_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("artifact_derivations")
    op.drop_table("shared_artifact_references")
    op.drop_index("ix_sharing_grant_lookup", table_name="artifact_sharing_grants")
    op.drop_table("artifact_sharing_grants")
