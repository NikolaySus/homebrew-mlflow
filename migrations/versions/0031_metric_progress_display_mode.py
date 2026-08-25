"""Store the display mode for the project default Progress metric."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_progress_display_mode"
down_revision: str | None = "0030_metric_progress_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column(
            "default_progress_metric_mode",
            sa.String(length=24),
            nullable=False,
            server_default="default",
        ),
    )
    op.create_check_constraint(
        "ck_research_projects_progress_metric_mode",
        "research_projects",
        "default_progress_metric_mode IN ('default', 'minimize', 'maximize')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_projects_progress_metric_mode",
        "research_projects",
        type_="check",
    )
    op.drop_column("research_projects", "default_progress_metric_mode")
