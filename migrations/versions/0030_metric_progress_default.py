"""Store the project-wide default metric for Progress views."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_metric_progress_default"
down_revision: str | None = "0029_model_signatures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column("default_progress_metric_key", sa.String(length=250), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_projects", "default_progress_metric_key")
