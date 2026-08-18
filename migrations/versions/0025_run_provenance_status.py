"""Persist explicit Run provenance quality and DVC experiment identity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_run_provenance_status"
down_revision: str | None = "0024_gitlab_identity_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "provenance_status",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("dvc_experiment_revision", sa.String(length=40), nullable=True),
    )

    runs = sa.table(
        "runs",
        sa.column("id", sa.Uuid()),
        sa.column("ended_at", sa.DateTime(timezone=True)),
        sa.column("git_commit_sha", sa.String()),
        sa.column("finalization_evidence", sa.JSON()),
        sa.column("provenance_status", sa.String()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            runs.c.id,
            runs.c.ended_at,
            runs.c.git_commit_sha,
            runs.c.finalization_evidence,
        )
    ).mappings()
    for row in rows:
        evidence = row["finalization_evidence"]
        provenance_error = evidence.get("provenance_error") if isinstance(evidence, dict) else None
        if row["ended_at"] is None:
            status = "pending"
        elif row["git_commit_sha"] is not None and not provenance_error:
            status = "complete"
        else:
            status = "invalid"
        connection.execute(
            runs.update().where(runs.c.id == row["id"]).values(provenance_status=status)
        )
    op.alter_column("runs", "provenance_status", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "dvc_experiment_revision")
    op.drop_column("runs", "provenance_status")
