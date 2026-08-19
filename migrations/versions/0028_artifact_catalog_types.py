"""Add typed artifacts, stable version numbers, and audited aliases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_artifact_catalog_types"
down_revision: str | None = "0027_mlflow_browser_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="generic"),
    )
    op.add_column("artifacts", sa.Column("description", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_artifacts_kind",
        "artifacts",
        "kind IN ('dataset', 'model', 'checkpoint', 'report', 'generic')",
    )
    op.create_unique_constraint(
        "uq_artifacts_project_name", "artifacts", ["owning_project_id", "name"]
    )

    op.add_column("artifact_versions", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column(
        "artifact_versions", sa.Column("mlflow_model_id", sa.String(length=34), nullable=True)
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY artifact_id ORDER BY published_at, public_id
                   ) AS version_sequence
            FROM artifact_versions
        )
        UPDATE artifact_versions AS versions
        SET sequence = ranked.version_sequence,
            mlflow_model_id = 'm-' || md5(versions.public_id)
        FROM ranked
        WHERE ranked.id = versions.id
        """
    )
    op.alter_column("artifact_versions", "sequence", nullable=False)
    op.alter_column("artifact_versions", "mlflow_model_id", nullable=False)
    op.create_unique_constraint(
        "uq_artifact_versions_artifact_sequence",
        "artifact_versions",
        ["artifact_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_artifact_versions_mlflow_model_id", "artifact_versions", ["mlflow_model_id"]
    )

    op.create_table(
        "artifact_aliases",
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["artifact_version_id"], ["artifact_versions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["principals.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["principals.id"]),
        sa.PrimaryKeyConstraint("artifact_id", "alias"),
    )
    op.create_index(
        "ix_artifact_aliases_version", "artifact_aliases", ["artifact_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_aliases_version", table_name="artifact_aliases")
    op.drop_table("artifact_aliases")
    op.drop_constraint(
        "uq_artifact_versions_mlflow_model_id", "artifact_versions", type_="unique"
    )
    op.drop_constraint(
        "uq_artifact_versions_artifact_sequence", "artifact_versions", type_="unique"
    )
    op.drop_column("artifact_versions", "mlflow_model_id")
    op.drop_column("artifact_versions", "sequence")
    op.drop_constraint("uq_artifacts_project_name", "artifacts", type_="unique")
    op.drop_constraint("ck_artifacts_kind", "artifacts", type_="check")
    op.drop_column("artifacts", "description")
    op.drop_column("artifacts", "kind")
