"""Store immutable model interface signatures on Artifact Versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_model_signatures"
down_revision: str | None = "0028_artifact_catalog_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifact_versions",
        sa.Column("model_signature", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("model_signature_sha256", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_artifact_versions_model_signature_pair",
        "artifact_versions",
        "(model_signature IS NULL) = (model_signature_sha256 IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_artifact_versions_model_signature_pair", "artifact_versions", type_="check"
    )
    op.drop_column("artifact_versions", "model_signature_sha256")
    op.drop_column("artifact_versions", "model_signature")
