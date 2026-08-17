"""Persist finalization evidence and exact Run inputs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_run_provenance"
down_revision: str | None = "0013_publication_event_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("finalization_evidence", sa.JSON()))
    op.create_table(
        "run_artifact_inputs",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column(
            "artifact_version_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_versions.id"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("run_artifact_inputs")
    op.drop_column("runs", "finalization_evidence")
