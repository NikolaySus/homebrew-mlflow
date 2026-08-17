"""Add durable Git repository provisioning state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_git_repositories"
down_revision: str | None = "0003_publication_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_repositories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("default_branch", sa.String(100), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("provider_id", sa.String(100)),
        sa.Column("web_url", sa.String(1000)),
        sa.Column("http_clone_url", sa.String(1000)),
        sa.Column("ssh_clone_url", sa.String(1000)),
        sa.Column("failure_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(100)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("project_id", "slug", name="uq_repository_project_slug"),
        sa.CheckConstraint(
            "state IN ('provisioning', 'active', 'failed', 'archived')",
            name="ck_git_repository_state",
        ),
    )
    op.create_index("ix_git_repository_provisioning", "git_repositories", ["state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_git_repository_provisioning", table_name="git_repositories")
    op.drop_table("git_repositories")
