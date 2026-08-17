"""Add immutable, secret-safe Environment Specifications and Run binding."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_environment_specs"
down_revision: str | None = "0020_pipeline_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environment_specifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("research_projects.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("canonical_document", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "name", name="uq_environment_specification_name"),
    )
    op.add_column("runs", sa.Column("environment_specification_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_runs_environment_specification",
        "runs",
        "environment_specifications",
        ["environment_specification_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_runs_environment_specification", "runs", type_="foreignkey")
    op.drop_column("runs", "environment_specification_id")
    op.drop_table("environment_specifications")
