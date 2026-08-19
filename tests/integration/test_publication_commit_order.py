from datetime import UTC, datetime
from uuid import uuid4

from homebrew_mlflow.application import ValidatedFile, ValidatedPublication
from homebrew_mlflow.domain import (
    DvcOutputIdentity,
    OutputKind,
    PublicationOperation,
    PublicationState,
    PublicId,
    ResourceKind,
)
from homebrew_mlflow.infrastructure.database import (
    ArtifactRow,
    ArtifactStorageLocationRow,
    ArtifactVersionFileRow,
    ArtifactVersionRow,
    Base,
    OrganizationRow,
    ResearchProjectRow,
    SqlAlchemyPublicationWorkStore,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def test_publication_flushes_version_before_foreign_key_children() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    organization_key, project_key, artifact_key = uuid4(), uuid4(), uuid4()
    organization_id = PublicId.generate(ResourceKind.ORGANIZATION)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    artifact_id = PublicId.generate(ResourceKind.ARTIFACT)

    with Session(engine) as session:
        session.add(
            OrganizationRow(
                id=organization_key,
                public_id=str(organization_id),
                name="Research",
                created_at=NOW,
                archived_at=None,
            )
        )
        session.flush()
        session.add(
            ResearchProjectRow(
                id=project_key,
                public_id=str(project_id),
                organization_id=organization_key,
                name="Publication",
                slug="publication",
                created_at=NOW,
                archived_at=None,
                state="active",
                gitlab_namespace_id=None,
                failure_code=None,
                updated_at=NOW,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
            )
        )
        session.flush()
        session.add(
            ArtifactRow(
                id=artifact_key,
                public_id=str(artifact_id),
                owning_project_id=project_key,
                name="Model",
                created_at=NOW,
                archived_at=None,
            )
        )
        session.commit()

        store = SqlAlchemyPublicationWorkStore(session)
        operation = PublicationOperation.queued(project_id, "key", "a" * 64, {})
        store.add_operation(operation)
        store.commit()
        for state, event_name in (
            (PublicationState.RESOLVING, "operation.resolving"),
            (PublicationState.VERIFYING, "validation.progress"),
            (PublicationState.COMMITTING, "operation.committing"),
        ):
            operation = store.advance(operation, state, event_name, NOW)

        version = store.publish(
            operation,
            ValidatedPublication(
                artifact_id,
                DvcOutputIdentity("md5", "b" * 32, OutputKind.FILE, 4, 1),
                (ValidatedFile("model.bin", 4, "b" * 32),),
                "research",
                "dvc/project/files/md5/bb/rest",
            ),
            NOW,
        )

        version_key = session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(version.id)
            )
        )
        assert version_key is not None
        assert session.scalar(
            select(func.count()).select_from(ArtifactVersionFileRow).where(
                ArtifactVersionFileRow.artifact_version_id == version_key
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ArtifactStorageLocationRow).where(
                ArtifactStorageLocationRow.artifact_version_id == version_key
            )
        ) == 1
