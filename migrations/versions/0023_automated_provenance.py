"""Allow reusable environment labels for automated immutable revisions."""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_automated_provenance"
down_revision: str | None = "0022_machine_credential_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_environment_specification_name",
        "environment_specifications",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_environment_specification_revision",
        "environment_specifications",
        ["project_id", "name", "kind", "sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_environment_specification_revision",
        "environment_specifications",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_environment_specification_name",
        "environment_specifications",
        ["project_id", "name"],
    )
