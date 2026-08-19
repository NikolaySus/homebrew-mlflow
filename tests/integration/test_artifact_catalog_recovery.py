from datetime import UTC, datetime, timedelta
from uuid import uuid4

from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    Base,
    SqlAlchemyArtifactCatalogUnitOfWork,
    SqlAlchemyRepositoryUnitOfWork,
)
from homebrew_mlflow.infrastructure.database import (
    ArtifactDerivationRow,
    ArtifactRow,
    ArtifactSharingGrantRow,
    ArtifactStorageLocationRow,
    ArtifactVersionFileRow,
    ArtifactVersionRow,
    OrganizationRow,
    PrincipalRow,
    ProjectMembershipRow,
    ResearchProjectRow,
    RunArtifactInputRow,
    RunRow,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def test_revoked_share_allows_only_a_completed_pre_revocation_input_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        actor, _owner, _consumer, version, completed_run, late_run, _derived = _seed(session)
        catalog = SqlAlchemyArtifactCatalogUnitOfWork(session)

        assert not catalog.version_accessible(version, actor)
        assert catalog.version_accessible(version, actor, completed_run)
        assert not catalog.version_accessible(version, actor, late_run)
        assert not catalog.version_accessible(
            version, actor, PublicId.generate(ResourceKind.RUN)
        )


def test_lineage_query_returns_edges_involving_the_exact_version() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _actor, _owner, _consumer, source, _completed_run, _late_run, derived = _seed(session)
        edges = SqlAlchemyArtifactCatalogUnitOfWork(session).derivations(source)

        assert len(edges) == 1
        assert edges[0].source_version_id == source
        assert edges[0].derived_version_id == derived


def test_recovery_dvc_policy_contains_only_the_completed_runs_exact_input_keys() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        actor, owner, consumer, _source, completed_run, _late_run, _derived = _seed(session)
        credentials = SqlAlchemyRepositoryUnitOfWork(session)

        assert credentials.shared_dvc_read_keys(
            consumer, actor, None, NOW + timedelta(hours=3)
        ) == ()
        assert credentials.shared_dvc_read_keys(
            consumer, actor, completed_run, NOW + timedelta(hours=3)
        ) == (
            f"dvc/{owner}/files/md5/aa/{'a' * 30}",
            f"dvc/{owner}/files/md5/aa/{'a' * 30}.root",
        )


def _seed(
    session: Session,
) -> tuple[PublicId, PublicId, PublicId, PublicId, PublicId, PublicId, PublicId]:
    organization_key, owner_key, consumer_key, actor_key = uuid4(), uuid4(), uuid4(), uuid4()
    artifact_key, source_key, derived_key = uuid4(), uuid4(), uuid4()
    organization = PublicId.generate(ResourceKind.ORGANIZATION)
    owner, consumer = (
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.PROJECT),
    )
    actor = PublicId.generate(ResourceKind.PRINCIPAL)
    artifact = PublicId.generate(ResourceKind.ARTIFACT)
    source = PublicId.generate(ResourceKind.ARTIFACT_VERSION)
    derived = PublicId.generate(ResourceKind.ARTIFACT_VERSION)
    completed_run, late_run = (
        PublicId.generate(ResourceKind.RUN),
        PublicId.generate(ResourceKind.RUN),
    )
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
                id=actor_key,
                public_id=str(actor),
                kind="human",
                display_name="Consumer",
                created_at=NOW,
                archived_at=None,
            ),
            *[
                ResearchProjectRow(
                    id=key,
                    public_id=str(public_id),
                    organization_id=organization_key,
                    name=name,
                    slug=name.lower(),
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
                for key, public_id, name in (
                    (owner_key, owner, "Owner"),
                    (consumer_key, consumer, "Consumer"),
                )
            ],
            ProjectMembershipRow(
                project_id=consumer_key,
                principal_id=actor_key,
                role="contributor",
                created_at=NOW,
            ),
            ArtifactRow(
                id=artifact_key,
                public_id=str(artifact),
                owning_project_id=owner_key,
                name="Source",
                created_at=NOW,
            ),
            *[
                ArtifactVersionRow(
                    id=key,
                    public_id=str(public_id),
                    artifact_id=artifact_key,
                    owning_project_id=owner_key,
                    publication_operation_id=uuid4(),
                    producing_run_id=None,
                    sequence=sequence,
                    algorithm="md5",
                    digest=digest * 32,
                    output_kind="file",
                    size=1,
                    file_count=1,
                    integrity="verified",
                    availability="available",
                    published_at=NOW,
                )
                for key, public_id, digest, sequence in (
                    (source_key, source, "a", 1),
                    (derived_key, derived, "b", 2),
                )
            ],
            ArtifactSharingGrantRow(
                id=uuid4(),
                public_id=str(PublicId.generate(ResourceKind.SHARING_GRANT)),
                artifact_version_id=source_key,
                owning_project_id=owner_key,
                consuming_project_id=consumer_key,
                created_by=actor_key,
                created_at=NOW,
                effective_at=NOW,
                revoked_at=NOW + timedelta(hours=2),
            ),
            ArtifactStorageLocationRow(
                artifact_version_id=source_key,
                bucket="research",
                object_key=f"dvc/{owner}/files/md5/aa/{'a' * 30}.root",
                created_at=NOW,
            ),
            ArtifactVersionFileRow(
                artifact_version_id=source_key,
                path="model.bin",
                size=1,
                digest="a" * 32,
            ),
            ArtifactDerivationRow(
                id=uuid4(),
                public_id=str(PublicId.generate(ResourceKind.DERIVATION)),
                source_version_id=source_key,
                derived_version_id=derived_key,
                created_by=actor_key,
                created_at=NOW + timedelta(minutes=30),
            ),
        ]
    )
    for public_id, ended_at in (
        (completed_run, NOW + timedelta(hours=1)),
        (late_run, NOW + timedelta(hours=3)),
    ):
        run_key = uuid4()
        session.add(
            RunRow(
                id=run_key,
                public_id=str(public_id),
                project_id=consumer_key,
                experiment_id=uuid4(),
                repository_id=uuid4(),
                creator_principal_id=actor_key,
                retry_of_run_id=None,
                state="succeeded",
                command=["python", "train.py"],
                created_at=NOW,
                started_at=NOW,
                heartbeat_at=ended_at,
                ended_at=ended_at,
                exit_code=0,
                finalization_digest="c" * 64,
                git_commit_sha="d" * 40,
                finalization_evidence={},
            )
        )
        session.add(RunArtifactInputRow(run_id=run_key, artifact_version_id=source_key))
    session.commit()
    return actor, owner, consumer, source, completed_run, late_run, derived
