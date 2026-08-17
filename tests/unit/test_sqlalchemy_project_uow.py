from datetime import UTC, datetime
from uuid import uuid4

from homebrew_mlflow.application import CreateProject, ProjectService
from homebrew_mlflow.domain import (
    Organization,
    OrganizationRole,
    Principal,
    PrincipalKind,
    PublicId,
    ResourceKind,
)
from homebrew_mlflow.infrastructure.database import (
    Base,
    OrganizationMembershipRow,
    OrganizationRow,
    PrincipalRow,
    SqlAlchemyProjectUnitOfWork,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_project_use_case_persists_one_audited_project_transaction() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    organization = Organization.create("Research")
    admin = Principal.create(PrincipalKind.HUMAN, "Admin")
    maintainer = Principal.create(PrincipalKind.HUMAN, "Maintainer")
    organization_key, admin_key, maintainer_key = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)

    with Session(engine) as session:
        session.add(
            OrganizationRow(
                id=organization_key,
                public_id=str(organization.id),
                name=organization.name,
                created_at=now,
                archived_at=None,
            )
        )
        session.add_all(
            [
                PrincipalRow(
                    id=key,
                    public_id=str(principal.id),
                    kind=principal.kind.value,
                    display_name=principal.display_name,
                    created_at=now,
                    archived_at=None,
                )
                for key, principal in ((admin_key, admin), (maintainer_key, maintainer))
            ]
        )
        session.add_all(
            [
                OrganizationMembershipRow(
                    organization_id=organization_key,
                    principal_id=key,
                    role=role.value,
                    created_at=now,
                )
                for key, role in (
                    (admin_key, OrganizationRole.ADMIN),
                    (maintainer_key, OrganizationRole.MEMBER),
                )
            ]
        )
        session.commit()

        project = ProjectService(SqlAlchemyProjectUnitOfWork(session)).create(
            admin.id,
            CreateProject(
                organization_id=organization.id,
                name="Models",
                slug="models",
                initial_maintainer_id=maintainer.id,
                request_id=PublicId.generate(ResourceKind.REQUEST),
            ),
        )
        assert session.scalar(
            select(OrganizationRow.id).where(OrganizationRow.public_id == str(organization.id))
        )
        assert project.slug == "models"
