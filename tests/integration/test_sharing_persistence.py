from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from homebrew_mlflow.application import ArtifactSharingService
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    Base,
    SqlAlchemyArtifactCatalogUnitOfWork,
    SqlAlchemySharingUnitOfWork,
)
from homebrew_mlflow.infrastructure.database import (
    ArtifactRow,
    ArtifactVersionRow,
    OrganizationRow,
    PrincipalRow,
    ProjectMembershipRow,
    PublicationOperationRow,
    ResearchProjectRow,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def test_exact_version_grant_reference_and_revocation_persist_atomically() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        owner, consumer, owner_actor, consumer_actor, version = seed(session)
        service = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session))

        grant = service.grant(owner_actor, version, consumer, NOW)
        reference = service.reference(consumer_actor, consumer, version, NOW)
        revoked = service.revoke(owner_actor, grant.id, NOW)

        assert reference.grant_id == grant.id
        assert revoked.revoked_at == NOW
        assert SqlAlchemySharingUnitOfWork(session).grant(grant.id) == revoked
        catalog = SqlAlchemyArtifactCatalogUnitOfWork(session)
        assert catalog.version_metadata_accessible(version, consumer_actor)
        assert not catalog.version_accessible(version, consumer_actor)
        assert owner != consumer


def seed(session: Session) -> tuple[PublicId, PublicId, PublicId, PublicId, PublicId]:
    organization_key = uuid4()
    owner_key, consumer_key = uuid4(), uuid4()
    owner_actor_key, consumer_actor_key = uuid4(), uuid4()
    artifact_key, version_key, operation_key = uuid4(), uuid4(), uuid4()
    organization = PublicId.generate(ResourceKind.ORGANIZATION)
    owner = PublicId.generate(ResourceKind.PROJECT)
    consumer = PublicId.generate(ResourceKind.PROJECT)
    owner_actor = PublicId.generate(ResourceKind.PRINCIPAL)
    consumer_actor = PublicId.generate(ResourceKind.PRINCIPAL)
    artifact = PublicId.generate(ResourceKind.ARTIFACT)
    version = PublicId.generate(ResourceKind.ARTIFACT_VERSION)
    operation = PublicId.generate(ResourceKind.PUBLICATION)
    session.add_all(
        [
            OrganizationRow(
                id=organization_key,
                public_id=str(organization),
                name="Organization",
                created_at=NOW,
                archived_at=None,
            ),
            PrincipalRow(
                id=owner_actor_key,
                public_id=str(owner_actor),
                kind="human",
                display_name="Owner",
                created_at=NOW,
                archived_at=None,
            ),
            PrincipalRow(
                id=consumer_actor_key,
                public_id=str(consumer_actor),
                kind="human",
                display_name="Consumer",
                created_at=NOW,
                archived_at=None,
            ),
            ResearchProjectRow(
                id=owner_key,
                public_id=str(owner),
                organization_id=organization_key,
                name="Owner",
                slug="owner",
                created_at=NOW,
                archived_at=None,
                state="active",
                gitlab_namespace_id="1",
                failure_code=None,
                updated_at=NOW,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
            ),
            ResearchProjectRow(
                id=consumer_key,
                public_id=str(consumer),
                organization_id=organization_key,
                name="Consumer",
                slug="consumer",
                created_at=NOW,
                archived_at=None,
                state="active",
                gitlab_namespace_id="2",
                failure_code=None,
                updated_at=NOW,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
            ),
            ProjectMembershipRow(
                project_id=owner_key,
                principal_id=owner_actor_key,
                role="maintainer",
                created_at=NOW,
            ),
            ProjectMembershipRow(
                project_id=consumer_key,
                principal_id=consumer_actor_key,
                role="contributor",
                created_at=NOW,
            ),
            ArtifactRow(
                id=artifact_key,
                public_id=str(artifact),
                owning_project_id=owner_key,
                name="Model",
                created_at=NOW,
            ),
            PublicationOperationRow(
                id=operation_key,
                public_id=str(operation),
                project_id=owner_key,
                idempotency_key="seed",
                request_digest="a" * 64,
                request_payload={},
                state="published",
                created_at=NOW,
                updated_at=NOW,
                claimed_at=None,
                claimed_by=None,
                attempt=1,
                artifact_version_id=version_key,
                failure_code=None,
                events_expired_through=0,
            ),
            ArtifactVersionRow(
                id=version_key,
                public_id=str(version),
                artifact_id=artifact_key,
                owning_project_id=owner_key,
                publication_operation_id=operation_key,
                producing_run_id=None,
                algorithm="md5",
                digest="b" * 32,
                output_kind="file",
                size=4,
                file_count=1,
                integrity="verified",
                availability="available",
                published_at=NOW,
            ),
        ]
    )
    session.commit()
    return owner, consumer, owner_actor, consumer_actor, version
