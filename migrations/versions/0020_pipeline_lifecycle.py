"""Add immutable Pipeline Versions and archive state to durable catalog records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_pipeline_lifecycle"
down_revision: str | None = "0019_publication_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("artifacts", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("artifact_versions", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_table(
        "pipeline_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "name", name="uq_pipeline_definition_name"),
    )
    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "definition_id",
            sa.Uuid(),
            sa.ForeignKey("pipeline_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            sa.Uuid(),
            sa.ForeignKey("git_repositories.id"),
            nullable=False,
        ),
        sa.Column("git_commit_sha", sa.String(40), nullable=False),
        sa.Column("pipeline_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "definition_id",
            "repository_id",
            "git_commit_sha",
            "pipeline_path",
            name="uq_pipeline_version_source",
        ),
    )
    op.add_column("runs", sa.Column("pipeline_version_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_runs_pipeline_version",
        "runs",
        "pipeline_versions",
        ["pipeline_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_runs_pipeline_version", "runs", type_="foreignkey")
    op.drop_column("runs", "pipeline_version_id")
    op.drop_table("pipeline_versions")
    op.drop_table("pipeline_definitions")
    op.drop_column("artifact_versions", "archived_at")
    op.drop_column("artifacts", "archived_at")
    op.drop_column("experiments", "archived_at")
