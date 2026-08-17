"""Add durable Research Project provisioning state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_project_provisioning"
down_revision: str | None = "0006_installation_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column("state", sa.String(24), nullable=False, server_default="active"),
    )
    op.add_column("research_projects", sa.Column("gitlab_namespace_id", sa.String(100)))
    op.add_column("research_projects", sa.Column("failure_code", sa.String(100)))
    op.add_column("research_projects", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("research_projects", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("research_projects", sa.Column("claimed_by", sa.String(100)))
    op.add_column(
        "research_projects",
        sa.Column("provisioning_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_research_project_state",
        "research_projects",
        "state IN ('provisioning', 'active', 'failed', 'archived')",
    )
    op.create_index(
        "ix_research_project_provisioning", "research_projects", ["state", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_research_project_provisioning", table_name="research_projects")
    op.drop_constraint("ck_research_project_state", "research_projects", type_="check")
    op.drop_column("research_projects", "provisioning_attempt")
    op.drop_column("research_projects", "claimed_by")
    op.drop_column("research_projects", "claimed_at")
    op.drop_column("research_projects", "updated_at")
    op.drop_column("research_projects", "failure_code")
    op.drop_column("research_projects", "gitlab_namespace_id")
    op.drop_column("research_projects", "state")
