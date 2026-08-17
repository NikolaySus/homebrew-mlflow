from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast
from uuid import UUID, uuid4

from homebrew_mlflow.application import (
    AuditEventView,
    HostedNamespace,
    MeView,
    NewRefreshCredential,
    OrganizationPrincipalView,
    OrganizationRoleView,
    ProjectMembershipView,
    ProjectRoleView,
    RepositoryProvisioningJob,
    RetentionDependencies,
    RotationResult,
    RotationStatus,
    StoredMachineCredential,
    ValidatedFile,
    ValidatedPublication,
    artifact_version_from_validation,
)
from homebrew_mlflow.domain import (
    Artifact,
    ArtifactDerivation,
    ArtifactSharingGrant,
    ArtifactVersion,
    AuditEvent,
    AvailabilityState,
    DvcOutputIdentity,
    EnvironmentKind,
    EnvironmentSpecification,
    Experiment,
    GitRepository,
    IntegrityState,
    MachineScope,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    OutputKind,
    PipelineDefinition,
    PipelineVersion,
    Principal,
    PrincipalKind,
    ProjectMembership,
    ProjectRole,
    ProjectState,
    PublicationEvent,
    PublicationOperation,
    PublicationState,
    PublicId,
    RepositoryState,
    ResearchProject,
    ResourceKind,
    Run,
    RunAttachment,
    RunMetric,
    RunParameter,
    RunState,
    RunTag,
    SecretContext,
    SharedArtifactReference,
)
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    create_engine,
    delete,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrincipalRow(Base):
    __tablename__ = "principals"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitLabIdentityBindingRow(Base):
    __tablename__ = "gitlab_identity_bindings"

    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), unique=True)
    username: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchProjectRow(Base):
    __tablename__ = "research_projects"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), default=ProjectState.PROVISIONING.value)
    gitlab_namespace_id: Mapped[str | None] = mapped_column(String(100))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    provisioning_attempt: Mapped[int] = mapped_column(default=0)
    gitlab_reconciliation_state: Mapped[str] = mapped_column(String(24), default="pending")
    gitlab_reconciliation_error: Mapped[str | None] = mapped_column(String(100))
    gitlab_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gitlab_reconcile_attempt: Mapped[int] = mapped_column(default=0)


def _project_accepts_access(session: Session, project_key: UUID) -> bool:
    return (
        session.scalar(
            select(ResearchProjectRow.id).where(
                ResearchProjectRow.id == project_key,
                ResearchProjectRow.state != ProjectState.ARCHIVED.value,
            )
        )
        is not None
    )


class OrganizationMembershipRow(Base):
    __tablename__ = "organization_memberships"

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstallationClaimRow(Base):
    __tablename__ = "installation_claim"

    singleton: Mapped[bool] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectMembershipRow(Base):
    __tablename__ = "project_memberships"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"), primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GitRepositoryRow(Base):
    __tablename__ = "git_repositories"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    default_branch: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(24))
    provider_id: Mapped[str | None] = mapped_column(String(100))
    web_url: Mapped[str | None] = mapped_column(String(1000))
    http_clone_url: Mapped[str | None] = mapped_column(String(1000))
    ssh_clone_url: Mapped[str | None] = mapped_column(String(1000))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(default=0)


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnvironmentSpecificationRow(Base):
    __tablename__ = "environment_specifications"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24))
    canonical_document: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineDefinitionRow(Base):
    __tablename__ = "pipeline_definitions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineVersionRow(Base):
    __tablename__ = "pipeline_versions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    definition_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_definitions.id"))
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("git_repositories.id"))
    git_commit_sha: Mapped[str] = mapped_column(String(40))
    pipeline_path: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    experiment_id: Mapped[UUID] = mapped_column(ForeignKey("experiments.id"))
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("git_repositories.id"))
    creator_principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    retry_of_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    pipeline_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("pipeline_versions.id"))
    environment_specification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("environment_specifications.id")
    )
    state: Mapped[str] = mapped_column(String(24))
    command: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None]
    finalization_digest: Mapped[str | None] = mapped_column(String(64))
    git_commit_sha: Mapped[str | None] = mapped_column(String(64))
    finalization_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RunArtifactInputRow(Base):
    __tablename__ = "run_artifact_inputs"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_versions.id"), primary_key=True
    )


class RunParameterRow(Base):
    __tablename__ = "run_parameters"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(250), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunMetricRow(Base):
    __tablename__ = "run_metrics"

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    key: Mapped[str] = mapped_column(String(250), index=True)
    value: Mapped[float] = mapped_column(Float)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger)
    step: Mapped[int] = mapped_column(BigInteger)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunTagRow(Base):
    __tablename__ = "run_tags"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(250), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunAttachmentRow(Base):
    __tablename__ = "run_attachments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    sequence: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_principal_id: Mapped[UUID | None] = mapped_column(ForeignKey("principals.id"))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("research_projects.id"))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[str] = mapped_column(String(64))
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)


class HumanRefreshCredentialRow(Base):
    __tablename__ = "human_refresh_credentials"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    sequence: Mapped[int]
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MachineCredentialRow(Base):
    __tablename__ = "machine_credentials"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationOperationRow(Base):
    __tablename__ = "publication_operations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("principals.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(default=0)
    artifact_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifact_versions.id"))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    events_expired_through: Mapped[int] = mapped_column(default=0)


class PublicationEventRow(Base):
    __tablename__ = "publication_events"

    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_operations.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    event_name: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    owning_project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactVersionRow(Base):
    __tablename__ = "artifact_versions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"))
    owning_project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    publication_operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("publication_operations.id"), unique=True
    )
    producing_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    algorithm: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(64))
    output_kind: Mapped[str] = mapped_column(String(16))
    size: Mapped[int] = mapped_column(BigInteger)
    file_count: Mapped[int] = mapped_column(BigInteger)
    integrity: Mapped[str] = mapped_column(String(16))
    availability: Mapped[str] = mapped_column(String(16))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactVersionFileRow(Base):
    __tablename__ = "artifact_version_files"

    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_versions.id"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str | None] = mapped_column(String(64))


class ArtifactStorageLocationRow(Base):
    __tablename__ = "artifact_storage_locations"

    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_versions.id"), primary_key=True
    )
    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactSharingGrantRow(Base):
    __tablename__ = "artifact_sharing_grants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    artifact_version_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_versions.id"))
    owning_project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    consuming_project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SharedArtifactReferenceRow(Base):
    __tablename__ = "shared_artifact_references"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    artifact_version_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_versions.id"))
    grant_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_sharing_grants.id"))
    consuming_project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactDerivationRow(Base):
    __tablename__ = "artifact_derivations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_versions.id"))
    derived_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_versions.id"), unique=True
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecretContextRow(Base):
    __tablename__ = "secret_contexts"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("research_projects.id"), primary_key=True)
    infisical_project_id: Mapped[str] = mapped_column(String(200))
    environment_slug: Mapped[str] = mapped_column(String(100))
    secret_path: Mapped[str] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciliation_state: Mapped[str] = mapped_column(String(24))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_attempt: Mapped[int] = mapped_column(default=0)


class SqlAlchemyProjectUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _organization_key(self, public_id: PublicId) -> UUID | None:
        return self._session.scalar(
            select(OrganizationRow.id).where(OrganizationRow.public_id == str(public_id))
        )

    def _principal_key(self, public_id: PublicId) -> UUID | None:
        return self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(public_id))
        )

    def _project_key(self, public_id: PublicId) -> UUID | None:
        return self._session.scalar(
            select(ResearchProjectRow.id).where(ResearchProjectRow.public_id == str(public_id))
        )

    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        organization_key = self._organization_key(organization_id)
        principal_key = self._principal_key(principal_id)
        if organization_key is None or principal_key is None:
            return None
        role = self._session.scalar(
            select(OrganizationMembershipRow.role).where(
                OrganizationMembershipRow.organization_id == organization_key,
                OrganizationMembershipRow.principal_id == principal_key,
            )
        )
        return OrganizationRole(role) if role else None

    def projects_for_principal(self, principal_id: PublicId) -> tuple[ResearchProject, ...]:
        principal_key = self._principal_key(principal_id)
        rows = self._session.scalars(
            select(ResearchProjectRow)
            .join(ProjectMembershipRow, ProjectMembershipRow.project_id == ResearchProjectRow.id)
            .where(ProjectMembershipRow.principal_id == principal_key)
            .order_by(ResearchProjectRow.name, ResearchProjectRow.public_id)
        )
        return tuple(
            ResearchProject(
                PublicId(ResourceKind.PROJECT, row.public_id),
                PublicId(
                    ResourceKind.ORGANIZATION,
                    cast(
                        str,
                        self._session.scalar(
                            select(OrganizationRow.public_id).where(
                                OrganizationRow.id == row.organization_id
                            )
                        ),
                    ),
                ),
                row.name,
                row.slug,
                row.created_at,
                ProjectState(row.state),
                row.gitlab_namespace_id,
                row.failure_code,
                _utc(row.archived_at) if row.archived_at is not None else None,
            )
            for row in rows
        )

    def project(self, project_id: PublicId) -> ResearchProject | None:
        row = self._session.scalar(
            select(ResearchProjectRow).where(ResearchProjectRow.public_id == str(project_id))
        )
        if row is None:
            return None
        organization_id = self._session.scalar(
            select(OrganizationRow.public_id).where(OrganizationRow.id == row.organization_id)
        )
        if organization_id is None:
            raise RuntimeError("Research Project refers to a missing Organization")
        return ResearchProject(
            project_id,
            PublicId(ResourceKind.ORGANIZATION, organization_id),
            row.name,
            row.slug,
            _utc(row.created_at),
            ProjectState(row.state),
            row.gitlab_namespace_id,
            row.failure_code,
            _utc(row.archived_at) if row.archived_at is not None else None,
        )

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        project_key = self._project_key(project_id)
        principal_key = self._principal_key(principal_id)
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == project_key,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        return ProjectRole(role) if role else None

    def set_project_archived(
        self, project_id: PublicId, archived_at: datetime | None
    ) -> None:
        row = self._session.scalar(
            select(ResearchProjectRow).where(ResearchProjectRow.public_id == str(project_id))
        )
        if row is None:
            raise ValueError("Research Project does not exist")
        row.archived_at = archived_at
        row.state = (
            ProjectState.ARCHIVED.value if archived_at is not None else ProjectState.ACTIVE.value
        )
        row.updated_at = archived_at or datetime.now(UTC)

    def principal(self, principal_id: PublicId) -> Principal | None:
        row = self._session.scalar(
            select(PrincipalRow).where(PrincipalRow.public_id == str(principal_id))
        )
        if row is None:
            return None
        return Principal(
            id=PublicId(ResourceKind.PRINCIPAL, row.public_id),
            kind=PrincipalKind(row.kind),
            display_name=row.display_name,
            created_at=row.created_at,
        )

    def project_slug_exists(self, organization_id: PublicId, slug: str) -> bool:
        organization_key = self._organization_key(organization_id)
        if organization_key is None:
            return False
        return (
            self._session.scalar(
                select(ResearchProjectRow.id).where(
                    ResearchProjectRow.organization_id == organization_key,
                    ResearchProjectRow.slug == slug,
                )
            )
            is not None
        )

    def add_project(self, project: ResearchProject) -> None:
        organization_key = self._organization_key(project.organization_id)
        if organization_key is None:
            raise ValueError("organization does not exist")
        self._session.add(
            ResearchProjectRow(
                id=uuid4(),
                public_id=str(project.id),
                organization_id=organization_key,
                name=project.name,
                slug=project.slug,
                created_at=project.created_at,
                archived_at=None,
                state=project.state.value,
                gitlab_namespace_id=project.gitlab_namespace_id,
                failure_code=project.failure_code,
                updated_at=project.created_at,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
            )
        )
        self._session.flush()

    def add_membership(self, membership: ProjectMembership) -> None:
        project_key = self._project_key(membership.project_id)
        principal_key = self._principal_key(membership.principal_id)
        if project_key is None or principal_key is None:
            raise ValueError("membership resource does not exist")
        self._session.add(
            ProjectMembershipRow(
                project_id=project_key,
                principal_id=principal_key,
                role=membership.role.value,
                created_at=membership.created_at,
            )
        )

    def append_audit(self, event: AuditEvent) -> None:
        actor_key = self._principal_key(event.actor_principal_id)
        project_key = self._project_key(event.project_id) if event.project_id else None
        self._session.add(
            AuditEventRow(
                occurred_at=event.occurred_at,
                actor_principal_id=actor_key,
                project_id=project_key,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=str(event.resource_id) if event.resource_id else None,
                outcome=event.outcome,
                request_id=str(event.request_id),
                safe_metadata=event.safe_metadata,
            )
        )

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyMembershipUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = SqlAlchemyProjectUnitOfWork(session)

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        project_key = self._projects._project_key(project_id)
        principal_key = self._projects._principal_key(principal_id)
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == project_key,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        return ProjectRole(role) if role is not None else None

    def project_organization(self, project_id: PublicId) -> PublicId | None:
        value = self._session.scalar(
            select(OrganizationRow.public_id)
            .join(
                ResearchProjectRow,
                ResearchProjectRow.organization_id == OrganizationRow.id,
            )
            .where(ResearchProjectRow.public_id == str(project_id))
        )
        return PublicId(ResourceKind.ORGANIZATION, value) if value is not None else None

    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        return self._projects.organization_role(organization_id, principal_id)

    def principal(self, principal_id: PublicId) -> Principal | None:
        return self._projects.principal(principal_id)

    def belongs_to_organization(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> bool:
        organization_key = self._projects._organization_key(organization_id)
        principal_key = self._projects._principal_key(principal_id)
        if organization_key is None or principal_key is None:
            return False
        return (
            self._session.scalar(
                select(OrganizationMembershipRow.principal_id).where(
                    OrganizationMembershipRow.organization_id == organization_key,
                    OrganizationMembershipRow.principal_id == principal_key,
                )
            )
            is not None
        )

    def membership(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectMembership | None:
        project_key = self._projects._project_key(project_id)
        principal_key = self._projects._principal_key(principal_id)
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        row = self._session.get(ProjectMembershipRow, (project_key, principal_key))
        if row is None:
            return None
        return ProjectMembership(project_id, principal_id, ProjectRole(row.role), row.created_at)

    def memberships(self, project_id: PublicId) -> tuple[ProjectMembershipView, ...]:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return ()
        rows = self._session.execute(
            select(ProjectMembershipRow, PrincipalRow, GitLabIdentityBindingRow.username)
            .join(PrincipalRow, PrincipalRow.id == ProjectMembershipRow.principal_id)
            .outerjoin(
                GitLabIdentityBindingRow,
                GitLabIdentityBindingRow.principal_id == PrincipalRow.id,
            )
            .where(ProjectMembershipRow.project_id == project_key)
            .order_by(PrincipalRow.display_name, PrincipalRow.public_id)
        )
        return tuple(
            ProjectMembershipView(
                ProjectMembership(
                    project_id,
                    PublicId(ResourceKind.PRINCIPAL, principal.public_id),
                    ProjectRole(membership.role),
                    membership.created_at,
                ),
                Principal(
                    PublicId(ResourceKind.PRINCIPAL, principal.public_id),
                    PrincipalKind(principal.kind),
                    principal.display_name,
                    principal.created_at,
                ),
                username,
            )
            for membership, principal, username in rows
        )

    def maintainer_count(self, project_id: PublicId) -> int:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return 0
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(ProjectMembershipRow)
                .where(
                    ProjectMembershipRow.project_id == project_key,
                    ProjectMembershipRow.role == ProjectRole.MAINTAINER.value,
                )
            )
            or 0
        )

    def put_membership(self, membership: ProjectMembership) -> None:
        project_key = self._projects._project_key(membership.project_id)
        principal_key = self._projects._principal_key(membership.principal_id)
        if project_key is None or principal_key is None:
            raise ValueError("membership resource does not exist")
        row = self._session.get(ProjectMembershipRow, (project_key, principal_key))
        if row is None:
            self._session.add(
                ProjectMembershipRow(
                    project_id=project_key,
                    principal_id=principal_key,
                    role=membership.role.value,
                    created_at=membership.created_at,
                )
            )
        else:
            row.role = membership.role.value
        self._session.flush()

    def remove_membership(self, project_id: PublicId, principal_id: PublicId) -> None:
        project_key = self._projects._project_key(project_id)
        principal_key = self._projects._principal_key(principal_id)
        if project_key is not None and principal_key is not None:
            self._session.execute(
                delete(ProjectMembershipRow).where(
                    ProjectMembershipRow.project_id == project_key,
                    ProjectMembershipRow.principal_id == principal_key,
                )
            )

    def mark_reconciliation_pending(self, project_id: PublicId, changed_at: datetime) -> None:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            raise ValueError("project does not exist")
        project = self._session.get(ResearchProjectRow, project_key)
        if project is None:
            raise ValueError("project does not exist")
        project.gitlab_reconciliation_state = "pending"
        project.gitlab_reconciliation_error = None
        project.updated_at = changed_at
        context = self._session.get(SecretContextRow, project_key)
        if context is not None:
            context.reconciliation_state = "pending"
            context.last_error_code = None
            context.updated_at = changed_at

    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyAuditUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._memberships = SqlAlchemyMembershipUnitOfWork(session)

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        return self._memberships.project_role(project_id, principal_id)

    def events(
        self, project_id: PublicId, *, after_sequence: int, limit: int
    ) -> tuple[AuditEventView, ...]:
        project_key = self._memberships._projects._project_key(project_id)
        if project_key is None:
            return ()
        rows = self._session.execute(
            select(AuditEventRow, PrincipalRow.public_id)
            .outerjoin(PrincipalRow, PrincipalRow.id == AuditEventRow.actor_principal_id)
            .where(
                AuditEventRow.project_id == project_key,
                AuditEventRow.sequence > after_sequence,
            )
            .order_by(AuditEventRow.sequence)
            .limit(limit)
        )
        return tuple(
            AuditEventView(
                event.sequence,
                event.occurred_at,
                PublicId(ResourceKind.PRINCIPAL, actor_id) if actor_id is not None else None,
                project_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.outcome,
                PublicId(ResourceKind.REQUEST, event.request_id),
                event.safe_metadata,
            )
            for event, actor_id in rows
        )


class SqlAlchemyIdentityReadStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def me(self, principal_id: PublicId) -> MeView | None:
        principal = self._session.scalar(
            select(PrincipalRow).where(
                PrincipalRow.public_id == str(principal_id),
                PrincipalRow.archived_at.is_(None),
            )
        )
        if principal is None:
            return None
        organization_rows = self._session.execute(
            select(OrganizationRow.public_id, OrganizationMembershipRow.role)
            .join(
                OrganizationMembershipRow,
                OrganizationMembershipRow.organization_id == OrganizationRow.id,
            )
            .where(OrganizationMembershipRow.principal_id == principal.id)
        )
        project_rows = self._session.execute(
            select(ResearchProjectRow.public_id, ProjectMembershipRow.role)
            .join(
                ProjectMembershipRow,
                ProjectMembershipRow.project_id == ResearchProjectRow.id,
            )
            .where(ProjectMembershipRow.principal_id == principal.id)
        )
        return MeView(
            Principal(
                principal_id,
                PrincipalKind(principal.kind),
                principal.display_name,
                _utc(principal.created_at),
            ),
            tuple(
                OrganizationRoleView(
                    PublicId(ResourceKind.ORGANIZATION, organization_id),
                    OrganizationRole(role),
                )
                for organization_id, role in organization_rows
            ),
            tuple(
                ProjectRoleView(
                    PublicId(ResourceKind.PROJECT, project_id), ProjectRole(role)
                )
                for project_id, role in project_rows
            ),
        )

    def organization_for_principal(self, principal_id: PublicId) -> Organization | None:
        value = self._session.execute(
            select(OrganizationRow)
            .join(
                OrganizationMembershipRow,
                OrganizationMembershipRow.organization_id == OrganizationRow.id,
            )
            .join(PrincipalRow, PrincipalRow.id == OrganizationMembershipRow.principal_id)
            .where(
                PrincipalRow.public_id == str(principal_id),
                OrganizationRow.archived_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        if value is None:
            return None
        return Organization(
            PublicId(ResourceKind.ORGANIZATION, value.public_id),
            value.name,
            _utc(value.created_at),
        )


class SqlAlchemyOrganizationMembershipUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = SqlAlchemyProjectUnitOfWork(session)

    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        return self._projects.organization_role(organization_id, principal_id)

    def principal(self, principal_id: PublicId) -> Principal | None:
        return self._projects.principal(principal_id)

    def principals(
        self, organization_id: PublicId
    ) -> tuple[OrganizationPrincipalView, ...]:
        organization_key = self._projects._organization_key(organization_id)
        if organization_key is None:
            return ()
        rows = self._session.execute(
            select(
                PrincipalRow,
                GitLabIdentityBindingRow.username,
                OrganizationMembershipRow.role,
                OrganizationMembershipRow.created_at,
            )
            .outerjoin(
                GitLabIdentityBindingRow,
                GitLabIdentityBindingRow.principal_id == PrincipalRow.id,
            )
            .outerjoin(
                OrganizationMembershipRow,
                (OrganizationMembershipRow.principal_id == PrincipalRow.id)
                & (OrganizationMembershipRow.organization_id == organization_key),
            )
            .where(PrincipalRow.archived_at.is_(None))
            .order_by(PrincipalRow.display_name, PrincipalRow.public_id)
        )
        return tuple(
            OrganizationPrincipalView(
                Principal(
                    PublicId(ResourceKind.PRINCIPAL, principal.public_id),
                    PrincipalKind(principal.kind),
                    principal.display_name,
                    principal.created_at,
                ),
                username,
                OrganizationRole(role) if role is not None else None,
                created_at,
            )
            for principal, username, role, created_at in rows
        )

    def admin_count(self, organization_id: PublicId) -> int:
        organization_key = self._projects._organization_key(organization_id)
        if organization_key is None:
            return 0
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(OrganizationMembershipRow)
                .where(
                    OrganizationMembershipRow.organization_id == organization_key,
                    OrganizationMembershipRow.role == OrganizationRole.ADMIN.value,
                )
            )
            or 0
        )

    def put_organization_membership(self, membership: OrganizationMembership) -> None:
        organization_key = self._projects._organization_key(membership.organization_id)
        principal_key = self._projects._principal_key(membership.principal_id)
        if organization_key is None or principal_key is None:
            raise ValueError("organization membership resource does not exist")
        row = self._session.get(
            OrganizationMembershipRow, (organization_key, principal_key)
        )
        if row is None:
            self._session.add(
                OrganizationMembershipRow(
                    organization_id=organization_key,
                    principal_id=principal_key,
                    role=membership.role.value,
                    created_at=membership.created_at,
                )
            )
        else:
            row.role = membership.role.value
        self._session.flush()

    def remove_organization_membership(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> None:
        organization_key = self._projects._organization_key(organization_id)
        principal_key = self._projects._principal_key(principal_id)
        if organization_key is not None and principal_key is not None:
            self._session.execute(
                delete(OrganizationMembershipRow).where(
                    OrganizationMembershipRow.organization_id == organization_key,
                    OrganizationMembershipRow.principal_id == principal_key,
                )
            )

    def append_audit(self, event: AuditEvent) -> None:
        self._projects.append_audit(event)

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyGitLabIdentityStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_or_create(
        self,
        subject: str,
        username: str,
        display_name: str,
        now: datetime,
    ) -> Principal:
        binding = self._session.scalar(
            select(GitLabIdentityBindingRow).where(GitLabIdentityBindingRow.subject == subject)
        )
        if binding is not None:
            row = self._session.get(PrincipalRow, binding.principal_id)
            if row is None:
                raise RuntimeError("GitLab identity binding refers to a missing principal")
            binding.username = username
            binding.last_seen_at = now
            row.display_name = display_name
            self._session.commit()
            return Principal(
                PublicId(ResourceKind.PRINCIPAL, row.public_id),
                PrincipalKind(row.kind),
                row.display_name,
                row.created_at,
            )

        principal = Principal.create(PrincipalKind.HUMAN, display_name)
        principal_key = uuid4()
        self._session.add(
            PrincipalRow(
                id=principal_key,
                public_id=str(principal.id),
                kind=principal.kind.value,
                display_name=principal.display_name,
                created_at=principal.created_at,
                archived_at=None,
            )
        )
        # No ORM relationship links these rows, so establish the referenced
        # principal before inserting its identity binding.
        self._session.flush()
        self._session.add(
            GitLabIdentityBindingRow(
                principal_id=principal_key,
                subject=subject,
                username=username,
                created_at=now,
                last_seen_at=now,
            )
        )
        self._session.commit()
        return principal


class SqlAlchemySetupStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def is_claimed(self) -> bool:
        return self._session.get(InstallationClaimRow, True) is not None

    def add_claim(
        self,
        organization: Organization,
        membership: OrganizationMembership,
        claimed_at: datetime,
    ) -> None:
        principal_key = self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(membership.principal_id))
        )
        if principal_key is None:
            raise ValueError("installation claimant does not exist")
        organization_key = uuid4()
        self._session.add(
            OrganizationRow(
                id=organization_key,
                public_id=str(organization.id),
                name=organization.name,
                created_at=organization.created_at,
                archived_at=None,
            )
        )
        # The rows below reference this organization, but the persistence
        # model intentionally has no ORM relationships to order the inserts.
        self._session.flush()
        self._session.add(
            OrganizationMembershipRow(
                organization_id=organization_key,
                principal_id=principal_key,
                role=membership.role.value,
                created_at=membership.created_at,
            )
        )
        self._session.add(
            InstallationClaimRow(
                singleton=True,
                organization_id=organization_key,
                principal_id=principal_key,
                claimed_at=claimed_at,
            )
        )

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyProvisioningStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_next(self, worker_id: str) -> RepositoryProvisioningJob | None:
        row = self._session.execute(
            select(GitRepositoryRow, ResearchProjectRow)
            .join(ResearchProjectRow, GitRepositoryRow.project_id == ResearchProjectRow.id)
            .where(
                GitRepositoryRow.state == "provisioning",
                GitRepositoryRow.claimed_at.is_(None),
                ResearchProjectRow.state.in_(("provisioning", "active")),
            )
            .order_by(GitRepositoryRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if row is None:
            return None
        repository, project = row
        now = datetime.now(UTC)
        repository.claimed_at = now
        repository.claimed_by = worker_id
        repository.attempt += 1
        project.claimed_at = now
        project.claimed_by = worker_id
        project.provisioning_attempt += 1
        self._session.commit()
        return RepositoryProvisioningJob(
            project_id=PublicId(ResourceKind.PROJECT, project.public_id),
            project_name=project.name,
            project_slug=project.slug,
            repository_id=PublicId(ResourceKind.REPOSITORY, repository.public_id),
            repository_name=repository.name,
            repository_slug=repository.slug,
            default_branch=repository.default_branch,
            namespace_id=project.gitlab_namespace_id,
            repository_provider_id=repository.provider_id,
        )

    def complete(
        self,
        job: RepositoryProvisioningJob,
        namespace: HostedNamespace,
        repository_provider_id: str,
        repository_default_branch: str,
        web_url: str,
        http_clone_url: str,
        ssh_clone_url: str,
    ) -> None:
        project = self._session.scalar(
            select(ResearchProjectRow).where(ResearchProjectRow.public_id == str(job.project_id))
        )
        repository = self._session.scalar(
            select(GitRepositoryRow).where(GitRepositoryRow.public_id == str(job.repository_id))
        )
        if project is None or repository is None:
            raise RuntimeError("claimed provisioning resources disappeared")
        now = datetime.now(UTC)
        project.state = "active"
        project.gitlab_namespace_id = namespace.provider_id
        project.failure_code = None
        project.updated_at = now
        project.claimed_at = None
        project.claimed_by = None
        repository.state = "active"
        repository.provider_id = repository_provider_id
        repository.default_branch = repository_default_branch
        repository.web_url = web_url
        repository.http_clone_url = http_clone_url
        repository.ssh_clone_url = ssh_clone_url
        repository.failure_code = None
        repository.updated_at = now
        repository.claimed_at = None
        repository.claimed_by = None
        self._session.commit()

    def fail(
        self,
        job: RepositoryProvisioningJob,
        failure_code: str,
        namespace_id: str | None,
        repository_provider_id: str | None,
    ) -> None:
        project = self._session.scalar(
            select(ResearchProjectRow).where(ResearchProjectRow.public_id == str(job.project_id))
        )
        repository = self._session.scalar(
            select(GitRepositoryRow).where(GitRepositoryRow.public_id == str(job.repository_id))
        )
        if project is None or repository is None:
            raise RuntimeError("claimed provisioning resources disappeared")
        now = datetime.now(UTC)
        if project.state == "provisioning":
            project.state = "failed"
            project.failure_code = failure_code
        project.gitlab_namespace_id = namespace_id or project.gitlab_namespace_id
        project.updated_at = now
        project.claimed_at = None
        project.claimed_by = None
        repository.state = "failed"
        repository.failure_code = failure_code
        repository.provider_id = repository_provider_id or repository.provider_id
        repository.updated_at = now
        repository.claimed_at = None
        repository.claimed_by = None
        self._session.commit()


class SqlAlchemyRepositoryUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _project_key(self, public_id: PublicId) -> UUID | None:
        return self._session.scalar(
            select(ResearchProjectRow.id).where(ResearchProjectRow.public_id == str(public_id))
        )

    def _principal_key(self, public_id: PublicId) -> UUID | None:
        return self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(public_id))
        )

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        project_key = self._project_key(project_id)
        principal_key = self._principal_key(principal_id)
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == project_key,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        return ProjectRole(role) if role else None

    def repository_slug_exists(self, project_id: PublicId, slug: str) -> bool:
        project_key = self._project_key(project_id)
        if project_key is None:
            return False
        return (
            self._session.scalar(
                select(GitRepositoryRow.id).where(
                    GitRepositoryRow.project_id == project_key,
                    GitRepositoryRow.slug == slug,
                )
            )
            is not None
        )

    def shared_dvc_read_keys(
        self,
        project_id: PublicId,
        principal_id: PublicId,
        recovery_run_id: PublicId | None,
        at: datetime,
    ) -> tuple[str, ...]:
        project_key = self._project_key(project_id)
        principal_key = self._principal_key(principal_id)
        if project_key is None or principal_key is None:
            return ()
        if self.project_role(project_id, principal_id) is None:
            return ()
        version_keys = set(
            self._session.scalars(
                select(ArtifactVersionRow.id)
                .join(
                    ArtifactSharingGrantRow,
                    ArtifactSharingGrantRow.artifact_version_id == ArtifactVersionRow.id,
                )
                .where(
                    ArtifactSharingGrantRow.consuming_project_id == project_key,
                    ArtifactSharingGrantRow.effective_at <= at,
                    ArtifactSharingGrantRow.revoked_at.is_(None),
                    ArtifactVersionRow.availability == AvailabilityState.AVAILABLE.value,
                )
            )
        )
        if recovery_run_id is not None:
            run = self._session.execute(
                select(RunRow.id, RunRow.ended_at)
                .join(
                    ProjectMembershipRow,
                    ProjectMembershipRow.project_id == RunRow.project_id,
                )
                .where(
                    RunRow.public_id == str(recovery_run_id),
                    RunRow.project_id == project_key,
                    RunRow.state.in_(
                        (
                            RunState.SUCCEEDED.value,
                            RunState.FAILED.value,
                            RunState.INTERRUPTED.value,
                        )
                    ),
                    RunRow.ended_at.is_not(None),
                    ProjectMembershipRow.principal_id == principal_key,
                )
            ).one_or_none()
            if run is not None and run.ended_at is not None:
                version_keys.update(
                    self._session.scalars(
                        select(RunArtifactInputRow.artifact_version_id)
                        .join(
                            ArtifactSharingGrantRow,
                            ArtifactSharingGrantRow.artifact_version_id
                            == RunArtifactInputRow.artifact_version_id,
                        )
                        .where(
                            RunArtifactInputRow.run_id == run.id,
                            ArtifactSharingGrantRow.consuming_project_id == project_key,
                            ArtifactSharingGrantRow.effective_at <= run.ended_at,
                            or_(
                                ArtifactSharingGrantRow.revoked_at.is_(None),
                                ArtifactSharingGrantRow.revoked_at > run.ended_at,
                            ),
                        )
                    )
                )
        if not version_keys:
            return ()
        keys = set(
            self._session.scalars(
                select(ArtifactStorageLocationRow.object_key).where(
                    ArtifactStorageLocationRow.artifact_version_id.in_(version_keys)
                )
            )
        )
        children = self._session.execute(
            select(
                ResearchProjectRow.public_id,
                ArtifactVersionRow.algorithm,
                ArtifactVersionFileRow.digest,
            )
            .select_from(ArtifactVersionFileRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactVersionFileRow.artifact_version_id,
            )
            .join(
                ResearchProjectRow,
                ResearchProjectRow.id == ArtifactVersionRow.owning_project_id,
            )
            .where(
                ArtifactVersionFileRow.artifact_version_id.in_(version_keys),
                ArtifactVersionFileRow.digest.is_not(None),
            )
        )
        for owner_id, algorithm, digest in children:
            if digest is not None:
                keys.add(f"dvc/{owner_id}/files/{algorithm}/{digest[:2]}/{digest[2:]}")
        return tuple(sorted(keys))

    def add_repository(self, repository: GitRepository) -> None:
        project_key = self._project_key(repository.project_id)
        if project_key is None:
            raise ValueError("repository project does not exist")
        self._session.add(
            GitRepositoryRow(
                id=uuid4(),
                public_id=str(repository.id),
                project_id=project_key,
                name=repository.name,
                slug=repository.slug,
                default_branch=repository.default_branch,
                state=repository.state.value,
                provider_id=repository.provider_id,
                web_url=repository.web_url,
                http_clone_url=repository.http_clone_url,
                ssh_clone_url=repository.ssh_clone_url,
                failure_code=repository.failure_code,
                created_at=repository.created_at,
                updated_at=repository.created_at,
                claimed_at=None,
                claimed_by=None,
                attempt=0,
            )
        )

    def repositories(self, project_id: PublicId) -> tuple[GitRepository, ...]:
        project_key = self._project_key(project_id)
        if project_key is None:
            return ()
        rows = self._session.scalars(
            select(GitRepositoryRow)
            .where(GitRepositoryRow.project_id == project_key)
            .order_by(GitRepositoryRow.name, GitRepositoryRow.public_id)
        )
        return tuple(
            GitRepository(
                PublicId(ResourceKind.REPOSITORY, row.public_id),
                project_id,
                row.name,
                row.slug,
                row.default_branch,
                RepositoryState(row.state),
                row.created_at,
                row.provider_id,
                row.web_url,
                row.http_clone_url,
                row.ssh_clone_url,
                row.failure_code,
            )
            for row in rows
        )

    def repository(self, repository_id: PublicId) -> GitRepository | None:
        row = self._session.scalar(
            select(GitRepositoryRow).where(GitRepositoryRow.public_id == str(repository_id))
        )
        if row is None:
            return None
        project_id = self._session.scalar(
            select(ResearchProjectRow.public_id).where(ResearchProjectRow.id == row.project_id)
        )
        if project_id is None:
            raise RuntimeError("repository refers to a missing project")
        return GitRepository(
            PublicId(ResourceKind.REPOSITORY, row.public_id),
            PublicId(ResourceKind.PROJECT, project_id),
            row.name,
            row.slug,
            row.default_branch,
            RepositoryState(row.state),
            row.created_at,
            row.provider_id,
            row.web_url,
            row.http_clone_url,
            row.ssh_clone_url,
            row.failure_code,
        )

    def save_repository(self, repository: GitRepository) -> None:
        row = self._session.scalar(
            select(GitRepositoryRow).where(GitRepositoryRow.public_id == str(repository.id))
        )
        if row is None:
            raise ValueError("repository does not exist")
        row.state = repository.state.value
        row.failure_code = repository.failure_code

    def retry_provisioning(self, repository: GitRepository) -> None:
        row = self._session.scalar(
            select(GitRepositoryRow).where(GitRepositoryRow.public_id == str(repository.id))
        )
        if row is None:
            raise ValueError("repository does not exist")
        project = self._session.get(ResearchProjectRow, row.project_id)
        if project is None:
            raise ValueError("repository project does not exist")
        row.state = RepositoryState.PROVISIONING.value
        row.failure_code = None
        row.claimed_at = None
        row.claimed_by = None
        if project.state == ProjectState.FAILED.value:
            project.state = ProjectState.PROVISIONING.value
            project.failure_code = None
            project.claimed_at = None
            project.claimed_by = None

    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyPipelineUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = SqlAlchemyProjectUnitOfWork(session)
        self._repositories = SqlAlchemyRepositoryUnitOfWork(session)

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        return self._repositories.project_role(project_id, principal_id)

    def repository_project(self, repository_id: PublicId) -> PublicId | None:
        value = self._session.execute(
            select(ResearchProjectRow.public_id)
            .join(GitRepositoryRow, GitRepositoryRow.project_id == ResearchProjectRow.id)
            .where(GitRepositoryRow.public_id == str(repository_id))
        ).scalar_one_or_none()
        return PublicId(ResourceKind.PROJECT, value) if value is not None else None

    def definition(self, definition_id: PublicId) -> PipelineDefinition | None:
        row = self._session.execute(
            select(PipelineDefinitionRow, ResearchProjectRow.public_id)
            .join(ResearchProjectRow, ResearchProjectRow.id == PipelineDefinitionRow.project_id)
            .where(PipelineDefinitionRow.public_id == str(definition_id))
        ).one_or_none()
        return self._definition_value(*row) if row is not None else None

    def definition_by_name(
        self, project_id: PublicId, name: str
    ) -> PipelineDefinition | None:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return None
        row = self._session.scalar(
            select(PipelineDefinitionRow).where(
                PipelineDefinitionRow.project_id == project_key,
                func.lower(PipelineDefinitionRow.name) == name.lower(),
            )
        )
        return self._definition_value(row, str(project_id)) if row is not None else None

    def definition_name_exists(self, project_id: PublicId, name: str) -> bool:
        project_key = self._projects._project_key(project_id)
        return (
            project_key is not None
            and (self._session.scalar(
                select(func.count())
                .select_from(PipelineDefinitionRow)
                .where(
                    PipelineDefinitionRow.project_id == project_key,
                    func.lower(PipelineDefinitionRow.name) == name.lower(),
                )
            ) or 0)
            > 0
        )

    def version_exists(
        self, definition_id: PublicId, repository_id: PublicId, commit: str, path: str
    ) -> bool:
        return (
            (self._session.scalar(
                select(func.count())
                .select_from(PipelineVersionRow)
                .join(
                    PipelineDefinitionRow,
                    PipelineDefinitionRow.id == PipelineVersionRow.definition_id,
                )
                .join(
                    GitRepositoryRow,
                    GitRepositoryRow.id == PipelineVersionRow.repository_id,
                )
                .where(
                    PipelineDefinitionRow.public_id == str(definition_id),
                    GitRepositoryRow.public_id == str(repository_id),
                    PipelineVersionRow.git_commit_sha == commit,
                    PipelineVersionRow.pipeline_path == path,
                )
            ) or 0)
            > 0
        )

    def version_by_source(
        self, definition_id: PublicId, repository_id: PublicId, commit: str, path: str
    ) -> PipelineVersion | None:
        value = self._session.execute(
            select(PipelineVersionRow, GitRepositoryRow.public_id)
            .join(GitRepositoryRow, GitRepositoryRow.id == PipelineVersionRow.repository_id)
            .join(
                PipelineDefinitionRow,
                PipelineDefinitionRow.id == PipelineVersionRow.definition_id,
            )
            .where(
                PipelineDefinitionRow.public_id == str(definition_id),
                GitRepositoryRow.public_id == str(repository_id),
                PipelineVersionRow.git_commit_sha == commit,
                PipelineVersionRow.pipeline_path == path,
            )
        ).one_or_none()
        if value is None:
            return None
        row, repository_public_id = value
        return self._version_value(row, repository_public_id, definition_id)

    def definitions(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineDefinition, ...]:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return ()
        statement = select(PipelineDefinitionRow).where(
            PipelineDefinitionRow.project_id == project_key
        )
        if not include_archived:
            statement = statement.where(PipelineDefinitionRow.archived_at.is_(None))
        rows = self._session.scalars(statement.order_by(PipelineDefinitionRow.created_at))
        return tuple(self._definition_value(row, str(project_id)) for row in rows)

    def versions(
        self, definition_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineVersion, ...]:
        statement = (
            select(PipelineVersionRow, GitRepositoryRow.public_id)
            .join(
                PipelineDefinitionRow,
                PipelineDefinitionRow.id == PipelineVersionRow.definition_id,
            )
            .join(GitRepositoryRow, GitRepositoryRow.id == PipelineVersionRow.repository_id)
            .where(PipelineDefinitionRow.public_id == str(definition_id))
        )
        if not include_archived:
            statement = statement.where(PipelineVersionRow.archived_at.is_(None))
        rows = self._session.execute(statement.order_by(PipelineVersionRow.created_at))
        return tuple(
            self._version_value(row, repository_id, definition_id)
            for row, repository_id in rows
        )

    def add_definition(self, definition: PipelineDefinition) -> None:
        project_key = self._projects._project_key(definition.project_id)
        if project_key is None:
            raise ValueError("Research Project does not exist")
        self._session.add(
            PipelineDefinitionRow(
                id=uuid4(),
                public_id=str(definition.id),
                project_id=project_key,
                name=definition.name,
                created_at=definition.created_at,
                archived_at=definition.archived_at,
            )
        )

    def add_version(self, version: PipelineVersion) -> None:
        definition_key = self._session.scalar(
            select(PipelineDefinitionRow.id).where(
                PipelineDefinitionRow.public_id == str(version.definition_id)
            )
        )
        repository_key = self._session.scalar(
            select(GitRepositoryRow.id).where(
                GitRepositoryRow.public_id == str(version.repository_id)
            )
        )
        if definition_key is None or repository_key is None:
            raise ValueError("Pipeline Version source does not exist")
        self._session.add(
            PipelineVersionRow(
                id=uuid4(),
                public_id=str(version.id),
                definition_id=definition_key,
                repository_id=repository_key,
                git_commit_sha=version.git_commit_sha,
                pipeline_path=version.pipeline_path,
                content_sha256=version.content_sha256,
                created_at=version.created_at,
                archived_at=version.archived_at,
            )
        )

    def archive_definition(self, definition_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(PipelineDefinitionRow).where(
                PipelineDefinitionRow.public_id == str(definition_id)
            )
        )
        if row is None:
            raise ValueError("Pipeline Definition does not exist")
        row.archived_at = at

    def archive_version(self, version_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(PipelineVersionRow).where(PipelineVersionRow.public_id == str(version_id))
        )
        if row is None:
            raise ValueError("Pipeline Version does not exist")
        row.archived_at = at

    def append_audit(self, event: AuditEvent) -> None:
        self._projects.append_audit(event)

    def commit(self) -> None:
        self._session.commit()

    @staticmethod
    def _definition_value(
        row: PipelineDefinitionRow, project_id: str
    ) -> PipelineDefinition:
        return PipelineDefinition(
            PublicId(ResourceKind.PIPELINE_DEFINITION, row.public_id),
            PublicId(ResourceKind.PROJECT, project_id),
            row.name,
            _utc(row.created_at),
            _utc(row.archived_at) if row.archived_at is not None else None,
        )

    @staticmethod
    def _version_value(
        row: PipelineVersionRow, repository_id: str, definition_id: PublicId
    ) -> PipelineVersion:
        return PipelineVersion(
            PublicId(ResourceKind.PIPELINE_VERSION, row.public_id),
            definition_id,
            PublicId(ResourceKind.REPOSITORY, repository_id),
            row.git_commit_sha,
            row.pipeline_path,
            row.content_sha256,
            _utc(row.created_at),
            _utc(row.archived_at) if row.archived_at is not None else None,
        )


class SqlAlchemyEnvironmentUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = SqlAlchemyProjectUnitOfWork(session)
        self._repositories = SqlAlchemyRepositoryUnitOfWork(session)

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        return self._repositories.project_role(project_id, principal_id)

    def name_exists(self, project_id: PublicId, name: str) -> bool:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return False
        count = self._session.scalar(
            select(func.count()).select_from(EnvironmentSpecificationRow).where(
                EnvironmentSpecificationRow.project_id == project_key,
                func.lower(EnvironmentSpecificationRow.name) == name.lower(),
            )
        )
        return (count or 0) > 0

    def specifications(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[EnvironmentSpecification, ...]:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return ()
        statement = select(EnvironmentSpecificationRow).where(
            EnvironmentSpecificationRow.project_id == project_key
        )
        if not include_archived:
            statement = statement.where(EnvironmentSpecificationRow.archived_at.is_(None))
        rows = self._session.scalars(statement.order_by(EnvironmentSpecificationRow.created_at))
        return tuple(self._value(row, project_id) for row in rows)

    def specification(
        self, specification_id: PublicId
    ) -> EnvironmentSpecification | None:
        value = self._session.execute(
            select(EnvironmentSpecificationRow, ResearchProjectRow.public_id)
            .join(
                ResearchProjectRow,
                ResearchProjectRow.id == EnvironmentSpecificationRow.project_id,
            )
            .where(EnvironmentSpecificationRow.public_id == str(specification_id))
        ).one_or_none()
        if value is None:
            return None
        row, project_id = value
        return self._value(row, PublicId(ResourceKind.PROJECT, project_id))

    def specification_by_revision(
        self, project_id: PublicId, name: str, kind: EnvironmentKind, sha256: str
    ) -> EnvironmentSpecification | None:
        project_key = self._projects._project_key(project_id)
        if project_key is None:
            return None
        row = self._session.scalar(
            select(EnvironmentSpecificationRow).where(
                EnvironmentSpecificationRow.project_id == project_key,
                func.lower(EnvironmentSpecificationRow.name) == name.lower(),
                EnvironmentSpecificationRow.kind == kind.value,
                EnvironmentSpecificationRow.sha256 == sha256,
            )
        )
        return self._value(row, project_id) if row is not None else None

    def add(self, specification: EnvironmentSpecification) -> None:
        project_key = self._projects._project_key(specification.project_id)
        if project_key is None:
            raise ValueError("Research Project does not exist")
        self._session.add(
            EnvironmentSpecificationRow(
                id=uuid4(),
                public_id=str(specification.id),
                project_id=project_key,
                name=specification.name,
                kind=specification.kind.value,
                canonical_document=specification.canonical_document,
                sha256=specification.sha256,
                created_at=specification.created_at,
                archived_at=specification.archived_at,
            )
        )

    def archive(self, specification_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(EnvironmentSpecificationRow).where(
                EnvironmentSpecificationRow.public_id == str(specification_id)
            )
        )
        if row is None:
            raise ValueError("Environment Specification does not exist")
        row.archived_at = at

    def append_audit(self, event: AuditEvent) -> None:
        self._projects.append_audit(event)

    def commit(self) -> None:
        self._session.commit()

    @staticmethod
    def _value(
        row: EnvironmentSpecificationRow, project_id: PublicId
    ) -> EnvironmentSpecification:
        return EnvironmentSpecification(
            PublicId(ResourceKind.ENVIRONMENT_SPECIFICATION, row.public_id),
            project_id,
            row.name,
            EnvironmentKind(row.kind),
            row.canonical_document,
            row.sha256,
            _utc(row.created_at),
            _utc(row.archived_at) if row.archived_at is not None else None,
        )


class SqlAlchemyRunUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _key(self, row_type: type[Any], public_id: PublicId) -> UUID | None:
        return self._session.scalar(select(row_type.id).where(row_type.public_id == str(public_id)))

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        project_key = self._key(ResearchProjectRow, project_id)
        principal_key = self._key(PrincipalRow, principal_id)
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == project_key,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        return ProjectRole(role) if role else None

    def repository_belongs_to_project(self, repository_id: PublicId, project_id: PublicId) -> bool:
        repository_key = self._key(GitRepositoryRow, repository_id)
        project_key = self._key(ResearchProjectRow, project_id)
        if repository_key is None or project_key is None:
            return False
        return (
            self._session.scalar(
                select(GitRepositoryRow.id).where(
                    GitRepositoryRow.id == repository_key,
                    GitRepositoryRow.project_id == project_key,
                    GitRepositoryRow.state == "active",
                )
            )
            is not None
        )

    def pipeline_version_belongs_to_project(
        self, pipeline_version_id: PublicId, project_id: PublicId
    ) -> bool:
        project_key = self._key(ResearchProjectRow, project_id)
        if project_key is None:
            return False
        return (
            self._session.scalar(
                select(PipelineVersionRow.id)
                .join(
                    PipelineDefinitionRow,
                    PipelineDefinitionRow.id == PipelineVersionRow.definition_id,
                )
                .where(
                    PipelineVersionRow.public_id == str(pipeline_version_id),
                    PipelineVersionRow.archived_at.is_(None),
                    PipelineDefinitionRow.project_id == project_key,
                    PipelineDefinitionRow.archived_at.is_(None),
                )
            )
            is not None
        )

    def environment_belongs_to_project(
        self, environment_id: PublicId, project_id: PublicId
    ) -> bool:
        project_key = self._key(ResearchProjectRow, project_id)
        if project_key is None:
            return False
        return (
            self._session.scalar(
                select(EnvironmentSpecificationRow.id).where(
                    EnvironmentSpecificationRow.public_id == str(environment_id),
                    EnvironmentSpecificationRow.project_id == project_key,
                    EnvironmentSpecificationRow.archived_at.is_(None),
                )
            )
            is not None
        )

    def experiment_by_name(self, project_id: PublicId, name: str) -> Experiment | None:
        project_key = self._key(ResearchProjectRow, project_id)
        if project_key is None:
            return None
        row = self._session.scalar(
            select(ExperimentRow).where(
                ExperimentRow.project_id == project_key,
                ExperimentRow.name == name,
            )
        )
        if row is None:
            return None
        return Experiment(
            PublicId(ResourceKind.EXPERIMENT, row.public_id),
            project_id,
            row.name,
            row.created_at,
            _utc(row.archived_at) if row.archived_at is not None else None,
        )

    def add_experiment(self, experiment: Experiment) -> None:
        project_key = self._key(ResearchProjectRow, experiment.project_id)
        if project_key is None:
            raise ValueError("Experiment project does not exist")
        self._session.add(
            ExperimentRow(
                id=uuid4(),
                public_id=str(experiment.id),
                project_id=project_key,
                name=experiment.name,
                created_at=experiment.created_at,
                archived_at=experiment.archived_at,
            )
        )
        self._session.flush()

    def add_run(self, run: Run) -> None:
        project_key = self._key(ResearchProjectRow, run.project_id)
        experiment_key = self._key(ExperimentRow, run.experiment_id)
        repository_key = self._key(GitRepositoryRow, run.repository_id)
        principal_key = self._key(PrincipalRow, run.creator_principal_id)
        retry_key = self._key(RunRow, run.retry_of_run_id) if run.retry_of_run_id else None
        pipeline_version_key = (
            self._key(PipelineVersionRow, run.pipeline_version_id)
            if run.pipeline_version_id
            else None
        )
        environment_key = (
            self._key(EnvironmentSpecificationRow, run.environment_specification_id)
            if run.environment_specification_id
            else None
        )
        if None in {project_key, experiment_key, repository_key, principal_key}:
            raise ValueError("Run references a missing resource")
        self._session.add(
            RunRow(
                id=uuid4(),
                public_id=str(run.id),
                project_id=project_key,
                experiment_id=experiment_key,
                repository_id=repository_key,
                creator_principal_id=principal_key,
                retry_of_run_id=retry_key,
                pipeline_version_id=pipeline_version_key,
                environment_specification_id=environment_key,
                state=run.state.value,
                command=list(run.command),
                created_at=run.created_at,
                started_at=run.started_at,
                heartbeat_at=run.heartbeat_at,
                ended_at=run.ended_at,
                exit_code=run.exit_code,
                finalization_digest=run.finalization_digest,
                git_commit_sha=run.git_commit_sha,
                finalization_evidence=run.finalization_evidence,
            )
        )

    def run(self, run_id: PublicId) -> Run | None:
        row = self._session.scalar(select(RunRow).where(RunRow.public_id == str(run_id)))
        if row is None:
            return None
        project_public_id = self._session.scalar(
            select(ResearchProjectRow.public_id).where(ResearchProjectRow.id == row.project_id)
        )
        experiment_public_id = self._session.scalar(
            select(ExperimentRow.public_id).where(ExperimentRow.id == row.experiment_id)
        )
        repository_public_id = self._session.scalar(
            select(GitRepositoryRow.public_id).where(GitRepositoryRow.id == row.repository_id)
        )
        principal_public_id = self._session.scalar(
            select(PrincipalRow.public_id).where(PrincipalRow.id == row.creator_principal_id)
        )
        retry_public_id = (
            self._session.scalar(select(RunRow.public_id).where(RunRow.id == row.retry_of_run_id))
            if row.retry_of_run_id
            else None
        )
        pipeline_version_public_id = (
            self._session.scalar(
                select(PipelineVersionRow.public_id).where(
                    PipelineVersionRow.id == row.pipeline_version_id
                )
            )
            if row.pipeline_version_id
            else None
        )
        environment_public_id = (
            self._session.scalar(
                select(EnvironmentSpecificationRow.public_id).where(
                    EnvironmentSpecificationRow.id == row.environment_specification_id
                )
            )
            if row.environment_specification_id
            else None
        )
        if (
            project_public_id is None
            or experiment_public_id is None
            or repository_public_id is None
            or principal_public_id is None
        ):
            raise RuntimeError("Run refers to missing canonical resources")
        return Run(
            PublicId(ResourceKind.RUN, row.public_id),
            PublicId(ResourceKind.PROJECT, project_public_id),
            PublicId(ResourceKind.EXPERIMENT, experiment_public_id),
            PublicId(ResourceKind.REPOSITORY, repository_public_id),
            PublicId(ResourceKind.PRINCIPAL, principal_public_id),
            RunState(row.state),
            tuple(row.command),
            row.created_at,
            row.started_at,
            row.heartbeat_at,
            row.ended_at,
            row.exit_code,
            row.finalization_digest,
            row.git_commit_sha,
            PublicId(ResourceKind.RUN, retry_public_id) if retry_public_id else None,
            row.finalization_evidence,
            PublicId(ResourceKind.PIPELINE_VERSION, pipeline_version_public_id)
            if pipeline_version_public_id
            else None,
            PublicId(ResourceKind.ENVIRONMENT_SPECIFICATION, environment_public_id)
            if environment_public_id
            else None,
        )

    def save_run(self, run: Run) -> None:
        row = self._session.scalar(select(RunRow).where(RunRow.public_id == str(run.id)))
        if row is None:
            raise ValueError("Run does not exist")
        row.state = run.state.value
        row.started_at = run.started_at
        row.heartbeat_at = run.heartbeat_at
        row.ended_at = run.ended_at
        row.exit_code = run.exit_code
        row.finalization_digest = run.finalization_digest
        row.git_commit_sha = run.git_commit_sha
        row.finalization_evidence = run.finalization_evidence
        row.pipeline_version_id = (
            self._key(PipelineVersionRow, run.pipeline_version_id)
            if run.pipeline_version_id is not None
            else None
        )
        row.environment_specification_id = (
            self._key(EnvironmentSpecificationRow, run.environment_specification_id)
            if run.environment_specification_id is not None
            else None
        )

    def artifact_version_available_to_project(
        self, version_id: PublicId, project_id: PublicId, at: datetime
    ) -> bool:
        project_key = self._key(ResearchProjectRow, project_id)
        version = self._session.scalar(
            select(ArtifactVersionRow).where(ArtifactVersionRow.public_id == str(version_id))
        )
        if project_key is None or version is None:
            return False
        if version.owning_project_id == project_key:
            return True
        return (
            self._session.scalar(
                select(ArtifactSharingGrantRow.id).where(
                    ArtifactSharingGrantRow.artifact_version_id == version.id,
                    ArtifactSharingGrantRow.consuming_project_id == project_key,
                    ArtifactSharingGrantRow.effective_at <= at,
                    (ArtifactSharingGrantRow.revoked_at.is_(None))
                    | (ArtifactSharingGrantRow.revoked_at > at),
                )
            )
            is not None
        )

    def add_run_input(
        self, run_id: PublicId, version_id: PublicId, occurred_at: datetime
    ) -> None:
        run_key = self._key(RunRow, run_id)
        version_key = self._key(ArtifactVersionRow, version_id)
        if run_key is None or version_key is None:
            raise ValueError("Run input refers to a missing resource")
        if self._session.get(RunArtifactInputRow, (run_key, version_key)) is None:
            self._session.add(RunArtifactInputRow(run_id=run_key, artifact_version_id=version_key))
        run_row = self._session.get(RunRow, run_key)
        version_row = self._session.get(ArtifactVersionRow, version_key)
        if (
            run_row is None
            or version_row is None
            or version_row.owning_project_id == run_row.project_id
        ):
            return
        grant = self._session.scalar(
            select(ArtifactSharingGrantRow).where(
                ArtifactSharingGrantRow.artifact_version_id == version_key,
                ArtifactSharingGrantRow.consuming_project_id == run_row.project_id,
                ArtifactSharingGrantRow.effective_at <= occurred_at,
                (ArtifactSharingGrantRow.revoked_at.is_(None))
                | (ArtifactSharingGrantRow.revoked_at > occurred_at),
            )
        )
        if grant is None:
            raise ValueError("shared Run input does not have an active grant")
        existing = self._session.scalar(
            select(SharedArtifactReferenceRow.id).where(
                SharedArtifactReferenceRow.artifact_version_id == version_key,
                SharedArtifactReferenceRow.run_id == run_key,
            )
        )
        if existing is None:
            self._session.add(
                SharedArtifactReferenceRow(
                    id=uuid4(),
                    public_id=str(PublicId.generate(ResourceKind.SHARED_REFERENCE)),
                    artifact_version_id=version_key,
                    grant_id=grant.id,
                    consuming_project_id=run_row.project_id,
                    created_by=run_row.creator_principal_id,
                    run_id=run_key,
                    created_at=occurred_at,
                )
            )

    def stale_running_runs(self, heartbeat_before: datetime) -> tuple[Run, ...]:
        public_ids = self._session.scalars(
            select(RunRow.public_id).where(
                RunRow.state == RunState.RUNNING.value,
                RunRow.heartbeat_at < heartbeat_before,
            )
        )
        runs = tuple(
            run
            for public_id in public_ids
            if (run := self.run(PublicId(ResourceKind.RUN, public_id))) is not None
        )
        return runs

    def runs_for_project(self, project_id: PublicId) -> tuple[Run, ...]:
        project_key = self._key(ResearchProjectRow, project_id)
        public_ids = self._session.scalars(
            select(RunRow.public_id)
            .where(RunRow.project_id == project_key)
            .order_by(RunRow.created_at.desc())
        )
        return tuple(
            run
            for value in public_ids
            if (run := self.run(PublicId(ResourceKind.RUN, value))) is not None
        )

    def experiments_for_project(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[Experiment, ...]:
        project_key = self._key(ResearchProjectRow, project_id)
        if project_key is None:
            return ()
        statement = select(ExperimentRow).where(ExperimentRow.project_id == project_key)
        if not include_archived:
            statement = statement.where(ExperimentRow.archived_at.is_(None))
        rows = self._session.scalars(
            statement.order_by(ExperimentRow.name, ExperimentRow.public_id)
        )
        return tuple(
            Experiment(
                PublicId(ResourceKind.EXPERIMENT, row.public_id),
                project_id,
                row.name,
                row.created_at,
                _utc(row.archived_at) if row.archived_at is not None else None,
            )
            for row in rows
        )

    def archive_experiment(self, experiment_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(ExperimentRow).where(ExperimentRow.public_id == str(experiment_id))
        )
        if row is None:
            raise ValueError("Experiment does not exist")
        row.archived_at = at

    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def run_inputs(self, run_id: PublicId) -> tuple[PublicId, ...]:
        values = self._session.scalars(
            select(ArtifactVersionRow.public_id)
            .join(
                RunArtifactInputRow,
                RunArtifactInputRow.artifact_version_id == ArtifactVersionRow.id,
            )
            .join(RunRow, RunRow.id == RunArtifactInputRow.run_id)
            .where(RunRow.public_id == str(run_id))
            .order_by(ArtifactVersionRow.public_id)
        )
        return tuple(PublicId(ResourceKind.ARTIFACT_VERSION, value) for value in values)

    def run_outputs(self, run_id: PublicId) -> tuple[PublicId, ...]:
        values = self._session.scalars(
            select(ArtifactVersionRow.public_id)
            .join(RunRow, RunRow.id == ArtifactVersionRow.producing_run_id)
            .where(RunRow.public_id == str(run_id))
            .order_by(ArtifactVersionRow.public_id)
        )
        return tuple(PublicId(ResourceKind.ARTIFACT_VERSION, value) for value in values)

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyTrackingUnitOfWork(SqlAlchemyRunUnitOfWork):
    def parameter(self, run_id: PublicId, key: str) -> RunParameter | None:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return None
        row = self._session.get(RunParameterRow, (run_key, key))
        if row is None:
            return None
        return RunParameter(run_id, row.key, row.value, row.logged_at)

    def add_parameter(self, parameter: RunParameter) -> None:
        run_key = self._key(RunRow, parameter.run_id)
        if run_key is None:
            raise ValueError("Run does not exist")
        self._session.add(
            RunParameterRow(
                run_id=run_key,
                key=parameter.key,
                value=parameter.value,
                logged_at=parameter.logged_at,
            )
        )

    def add_metric(self, metric: RunMetric) -> None:
        run_key = self._key(RunRow, metric.run_id)
        if run_key is None:
            raise ValueError("Run does not exist")
        self._session.add(
            RunMetricRow(
                run_id=run_key,
                key=metric.key,
                value=metric.value,
                timestamp_ms=metric.timestamp_ms,
                step=metric.step,
                logged_at=metric.logged_at,
            )
        )

    def upsert_tag(self, tag: RunTag) -> None:
        run_key = self._key(RunRow, tag.run_id)
        if run_key is None:
            raise ValueError("Run does not exist")
        row = self._session.get(RunTagRow, (run_key, tag.key))
        if row is None:
            self._session.add(
                RunTagRow(
                    run_id=run_key,
                    key=tag.key,
                    value=tag.value,
                    updated_at=tag.updated_at,
                )
            )
        else:
            row.value = tag.value
            row.updated_at = tag.updated_at

    def list_parameters(self, run_id: PublicId) -> tuple[RunParameter, ...]:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return ()
        rows = self._session.scalars(
            select(RunParameterRow)
            .where(RunParameterRow.run_id == run_key)
            .order_by(RunParameterRow.key)
        )
        return tuple(RunParameter(run_id, row.key, row.value, row.logged_at) for row in rows)

    def list_metrics(self, run_id: PublicId) -> tuple[RunMetric, ...]:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return ()
        rows = self._session.scalars(
            select(RunMetricRow)
            .where(RunMetricRow.run_id == run_key)
            .order_by(RunMetricRow.sequence)
        )
        return tuple(
            RunMetric(
                run_id,
                row.key,
                row.value,
                row.timestamp_ms,
                row.step,
                row.logged_at,
            )
            for row in rows
        )

    def list_tags(self, run_id: PublicId) -> tuple[RunTag, ...]:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return ()
        rows = self._session.scalars(
            select(RunTagRow).where(RunTagRow.run_id == run_key).order_by(RunTagRow.key)
        )
        return tuple(RunTag(run_id, row.key, row.value, row.updated_at) for row in rows)


class SqlAlchemyAttachmentUnitOfWork(SqlAlchemyRunUnitOfWork):
    def attachment(self, run_id: PublicId, path: str) -> RunAttachment | None:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return None
        row = self._session.scalar(
            select(RunAttachmentRow).where(
                RunAttachmentRow.run_id == run_key, RunAttachmentRow.path == path
            )
        )
        return self._attachment(row, run_id) if row is not None else None

    def list_attachments(self, run_id: PublicId) -> tuple[RunAttachment, ...]:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return ()
        rows = self._session.scalars(
            select(RunAttachmentRow)
            .where(RunAttachmentRow.run_id == run_key)
            .order_by(RunAttachmentRow.path)
        )
        return tuple(self._attachment(row, run_id) for row in rows)

    def attachment_totals(self, run_id: PublicId) -> tuple[int, int]:
        run_key = self._key(RunRow, run_id)
        if run_key is None:
            return (0, 0)
        row = self._session.execute(
            select(func.count(), func.coalesce(func.sum(RunAttachmentRow.size), 0)).where(
                RunAttachmentRow.run_id == run_key
            )
        ).one()
        return int(row[0]), int(row[1])

    def add_attachment(self, attachment: RunAttachment) -> None:
        run_key = self._key(RunRow, attachment.run_id)
        if run_key is None:
            raise ValueError("Run does not exist")
        self._session.add(
            RunAttachmentRow(
                id=uuid4(),
                run_id=run_key,
                path=attachment.path,
                size=attachment.size,
                media_type=attachment.media_type,
                sha256=attachment.sha256,
                object_key=attachment.object_key,
                created_at=attachment.created_at,
                purged_at=None,
            )
        )

    @staticmethod
    def _attachment(row: RunAttachmentRow, run_id: PublicId) -> RunAttachment:
        return RunAttachment(
            run_id,
            row.path,
            row.size,
            row.media_type,
            row.sha256,
            row.object_key,
            row.created_at,
            row.purged_at,
        )


class SqlAlchemyArtifactCatalogUnitOfWork(SqlAlchemyRepositoryUnitOfWork):
    def artifact_by_name(self, project_id: PublicId, name: str) -> Artifact | None:
        project_key = self._project_key(project_id)
        if project_key is None:
            return None
        row = self._session.scalar(
            select(ArtifactRow).where(
                ArtifactRow.owning_project_id == project_key, ArtifactRow.name == name
            )
        )
        if row is None:
            return None
        return Artifact(
            PublicId(ResourceKind.ARTIFACT, row.public_id),
            project_id,
            row.name,
            row.created_at,
            _utc(row.archived_at) if row.archived_at is not None else None,
        )

    def artifact(self, artifact_id: PublicId) -> Artifact | None:
        value = self._session.execute(
            select(ArtifactRow, ResearchProjectRow.public_id)
            .join(ResearchProjectRow, ResearchProjectRow.id == ArtifactRow.owning_project_id)
            .where(ArtifactRow.public_id == str(artifact_id))
        ).one_or_none()
        if value is None:
            return None
        row, project_id = value
        return Artifact(
            artifact_id,
            PublicId(ResourceKind.PROJECT, project_id),
            row.name,
            _utc(row.created_at),
            _utc(row.archived_at) if row.archived_at is not None else None,
        )

    def add_artifact(self, artifact: Artifact) -> None:
        project_key = self._project_key(artifact.owning_project_id)
        if project_key is None:
            raise ValueError("Artifact project does not exist")
        self._session.add(
            ArtifactRow(
                id=uuid4(),
                public_id=str(artifact.id),
                owning_project_id=project_key,
                name=artifact.name,
                created_at=artifact.created_at,
                archived_at=artifact.archived_at,
            )
        )

    def artifacts(self, project_id: PublicId) -> tuple[Artifact, ...]:
        project_key = self._project_key(project_id)
        rows = self._session.scalars(
            select(ArtifactRow)
            .where(
                ArtifactRow.owning_project_id == project_key,
                ArtifactRow.archived_at.is_(None),
            )
            .order_by(ArtifactRow.name, ArtifactRow.public_id)
        )
        return tuple(
            Artifact(
                PublicId(ResourceKind.ARTIFACT, row.public_id),
                project_id,
                row.name,
                row.created_at,
                None,
            )
            for row in rows
        )

    def version(self, version_id: PublicId) -> ArtifactVersion | None:
        row = self._session.scalar(
            select(ArtifactVersionRow).where(ArtifactVersionRow.public_id == str(version_id))
        )
        return self._version(row) if row is not None else None

    def versions(self, artifact_id: PublicId) -> tuple[ArtifactVersion, ...]:
        rows = self._session.scalars(
            select(ArtifactVersionRow)
            .join(ArtifactRow, ArtifactRow.id == ArtifactVersionRow.artifact_id)
            .where(ArtifactRow.public_id == str(artifact_id))
            .order_by(ArtifactVersionRow.published_at.desc())
        )
        return tuple(self._version(row) for row in rows)

    def version_files(self, version_id: PublicId) -> tuple[ValidatedFile, ...]:
        rows = self._session.scalars(
            select(ArtifactVersionFileRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactVersionFileRow.artifact_version_id,
            )
            .where(ArtifactVersionRow.public_id == str(version_id))
            .order_by(ArtifactVersionFileRow.path)
        )
        return tuple(ValidatedFile(row.path, row.size, row.digest) for row in rows)

    def version_accessible(
        self,
        version_id: PublicId,
        principal_id: PublicId,
        recovery_run_id: PublicId | None = None,
    ) -> bool:
        principal_key = self._principal_key(principal_id)
        version = self._session.scalar(
            select(ArtifactVersionRow).where(ArtifactVersionRow.public_id == str(version_id))
        )
        if (
            principal_key is None
            or version is None
            or not _project_accepts_access(self._session, version.owning_project_id)
        ):
            return False
        owner_role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == version.owning_project_id,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        if owner_role is not None:
            return True
        active_grant = (
            self._session.scalar(
                select(ArtifactSharingGrantRow.id)
                .join(
                    ProjectMembershipRow,
                    ProjectMembershipRow.project_id == ArtifactSharingGrantRow.consuming_project_id,
                )
                .where(
                    ArtifactSharingGrantRow.artifact_version_id == version.id,
                    ArtifactSharingGrantRow.revoked_at.is_(None),
                    ProjectMembershipRow.principal_id == principal_key,
                )
                .limit(1)
            )
            is not None
        )
        if active_grant or recovery_run_id is None:
            return active_grant
        run = self._session.execute(
            select(RunRow.project_id, RunRow.ended_at)
            .join(RunArtifactInputRow, RunArtifactInputRow.run_id == RunRow.id)
            .join(
                ProjectMembershipRow,
                ProjectMembershipRow.project_id == RunRow.project_id,
            )
            .where(
                RunRow.public_id == str(recovery_run_id),
                RunRow.state.in_(
                    (
                        RunState.SUCCEEDED.value,
                        RunState.FAILED.value,
                        RunState.INTERRUPTED.value,
                    )
                ),
                RunRow.ended_at.is_not(None),
                RunArtifactInputRow.artifact_version_id == version.id,
                ProjectMembershipRow.principal_id == principal_key,
            )
        ).one_or_none()
        if run is None or run.ended_at is None:
            return False
        return (
            self._session.scalar(
                select(ArtifactSharingGrantRow.id)
                .where(
                    ArtifactSharingGrantRow.artifact_version_id == version.id,
                    ArtifactSharingGrantRow.consuming_project_id == run.project_id,
                    ArtifactSharingGrantRow.effective_at <= run.ended_at,
                    or_(
                        ArtifactSharingGrantRow.revoked_at.is_(None),
                        ArtifactSharingGrantRow.revoked_at > run.ended_at,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    def version_metadata_accessible(
        self, version_id: PublicId, principal_id: PublicId
    ) -> bool:
        principal_key = self._principal_key(principal_id)
        version = self._session.scalar(
            select(ArtifactVersionRow).where(ArtifactVersionRow.public_id == str(version_id))
        )
        if principal_key is None or version is None:
            return False
        owner_role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == version.owning_project_id,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        if owner_role is not None:
            return True
        return (
            self._session.scalar(
                select(ArtifactSharingGrantRow.id)
                .join(
                    ProjectMembershipRow,
                    ProjectMembershipRow.project_id
                    == ArtifactSharingGrantRow.consuming_project_id,
                )
                .join(
                    ResearchProjectRow,
                    ResearchProjectRow.id == ArtifactSharingGrantRow.consuming_project_id,
                )
                .where(
                    ArtifactSharingGrantRow.artifact_version_id == version.id,
                    ProjectMembershipRow.principal_id == principal_key,
                    ResearchProjectRow.state == ProjectState.ACTIVE.value,
                )
                .limit(1)
            )
            is not None
        )

    def derivations(self, version_id: PublicId) -> tuple[ArtifactDerivation, ...]:
        version_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(ArtifactVersionRow.public_id == str(version_id))
        )
        if version_key is None:
            return ()
        rows = self._session.scalars(
            select(ArtifactDerivationRow)
            .where(
                or_(
                    ArtifactDerivationRow.source_version_id == version_key,
                    ArtifactDerivationRow.derived_version_id == version_key,
                )
            )
            .order_by(ArtifactDerivationRow.created_at, ArtifactDerivationRow.public_id)
        )
        sharing = SqlAlchemySharingUnitOfWork(self._session)
        return tuple(sharing._derivation(row) for row in rows)

    def pointer_output_path(self, version_id: PublicId) -> str | None:
        payload = self._session.scalar(
            select(PublicationOperationRow.request_payload)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.publication_operation_id == PublicationOperationRow.id,
            )
            .where(ArtifactVersionRow.public_id == str(version_id))
        )
        if not isinstance(payload, dict):
            return None
        selector = payload.get("selector")
        if not isinstance(selector, dict):
            return None
        output = selector.get("output")
        return output if isinstance(output, str) and output else None

    def retention_dependencies(self, version_id: PublicId) -> RetentionDependencies:
        version_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(version_id)
            )
        )
        if version_key is None:
            raise ValueError("Artifact Version does not exist")
        retained_runs = self._session.scalar(
            select(func.count()).select_from(RunArtifactInputRow).where(
                RunArtifactInputRow.artifact_version_id == version_key
            )
        ) or 0
        shared_references = self._session.scalar(
            select(func.count()).select_from(SharedArtifactReferenceRow).where(
                SharedArtifactReferenceRow.artifact_version_id == version_key
            )
        ) or 0
        derivatives = self._session.scalar(
            select(func.count()).select_from(ArtifactDerivationRow).where(
                or_(
                    ArtifactDerivationRow.source_version_id == version_key,
                    ArtifactDerivationRow.derived_version_id == version_key,
                )
            )
        ) or 0
        active_grants = self._session.scalar(
            select(func.count()).select_from(ArtifactSharingGrantRow).where(
                ArtifactSharingGrantRow.artifact_version_id == version_key,
                ArtifactSharingGrantRow.revoked_at.is_(None),
            )
        ) or 0
        locations = self._session.scalar(
            select(func.count()).select_from(ArtifactStorageLocationRow).where(
                ArtifactStorageLocationRow.artifact_version_id == version_key
            )
        ) or 0
        return RetentionDependencies(
            retained_runs=retained_runs,
            shared_references=shared_references,
            derivatives=derivatives,
            active_grants=active_grants,
            replicas=max(0, locations - 1),
        )

    def archive_artifact(self, artifact_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(ArtifactRow).where(ArtifactRow.public_id == str(artifact_id))
        )
        if row is None:
            raise ValueError("Artifact does not exist")
        row.archived_at = at

    def archive_version(self, version_id: PublicId, at: datetime) -> None:
        row = self._session.scalar(
            select(ArtifactVersionRow).where(ArtifactVersionRow.public_id == str(version_id))
        )
        if row is None:
            raise ValueError("Artifact Version does not exist")
        row.archived_at = at

    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def _version(self, row: ArtifactVersionRow) -> ArtifactVersion:
        artifact_id, project_id = self._session.execute(
            select(ArtifactRow.public_id, ResearchProjectRow.public_id)
            .select_from(ArtifactVersionRow)
            .join(ArtifactRow, ArtifactRow.id == row.artifact_id)
            .join(ResearchProjectRow, ResearchProjectRow.id == row.owning_project_id)
            .where(ArtifactVersionRow.id == row.id)
        ).one()
        return ArtifactVersion(
            PublicId(ResourceKind.ARTIFACT_VERSION, row.public_id),
            PublicId(ResourceKind.ARTIFACT, artifact_id),
            PublicId(ResourceKind.PROJECT, project_id),
            DvcOutputIdentity(
                row.algorithm,
                row.digest,
                OutputKind(row.output_kind),
                row.size,
                row.file_count,
            ),
            IntegrityState(row.integrity),
            AvailabilityState(row.availability),
            row.published_at,
            _utc(row.archived_at) if row.archived_at is not None else None,
        )


class SqlAlchemySharingUnitOfWork(SqlAlchemyRepositoryUnitOfWork):
    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def version_owner(self, version_id: PublicId) -> PublicId | None:
        value = self._session.scalar(
            select(ResearchProjectRow.public_id)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.owning_project_id == ResearchProjectRow.id,
            )
            .where(ArtifactVersionRow.public_id == str(version_id))
        )
        return PublicId(ResourceKind.PROJECT, value) if value is not None else None

    def project_exists(self, project_id: PublicId) -> bool:
        return self._project_key(project_id) is not None

    def grant_for_projects(
        self, version_id: PublicId, consuming_project_id: PublicId
    ) -> ArtifactSharingGrant | None:
        consuming_key = self._project_key(consuming_project_id)
        row = self._session.scalar(
            select(ArtifactSharingGrantRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactSharingGrantRow.artifact_version_id,
            )
            .where(
                ArtifactVersionRow.public_id == str(version_id),
                ArtifactSharingGrantRow.consuming_project_id == consuming_key,
            )
            .order_by(ArtifactSharingGrantRow.created_at.desc())
            .limit(1)
        )
        return self._grant(row) if row is not None else None

    def grant(self, grant_id: PublicId) -> ArtifactSharingGrant | None:
        row = self._session.scalar(
            select(ArtifactSharingGrantRow).where(
                ArtifactSharingGrantRow.public_id == str(grant_id)
            )
        )
        return self._grant(row) if row is not None else None

    def grants_for_version(self, version_id: PublicId) -> tuple[ArtifactSharingGrant, ...]:
        rows = self._session.scalars(
            select(ArtifactSharingGrantRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactSharingGrantRow.artifact_version_id,
            )
            .where(ArtifactVersionRow.public_id == str(version_id))
            .order_by(ArtifactSharingGrantRow.created_at, ArtifactSharingGrantRow.public_id)
        )
        return tuple(self._grant(row) for row in rows)

    def add_grant(self, grant: ArtifactSharingGrant) -> None:
        version_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(grant.version_id)
            )
        )
        owner_key = self._project_key(grant.owning_project_id)
        consumer_key = self._project_key(grant.consuming_project_id)
        actor_key = self._principal_key(grant.created_by)
        if None in {version_key, owner_key, consumer_key, actor_key}:
            raise ValueError("Sharing Grant refers to a missing resource")
        self._session.add(
            ArtifactSharingGrantRow(
                id=uuid4(),
                public_id=str(grant.id),
                artifact_version_id=version_key,
                owning_project_id=owner_key,
                consuming_project_id=consumer_key,
                created_by=actor_key,
                created_at=grant.created_at,
                effective_at=grant.effective_at,
                revoked_at=grant.revoked_at,
            )
        )

    def update_grant(self, grant: ArtifactSharingGrant) -> None:
        row = self._session.scalar(
            select(ArtifactSharingGrantRow).where(
                ArtifactSharingGrantRow.public_id == str(grant.id)
            )
        )
        if row is None:
            raise ValueError("Sharing Grant does not exist")
        row.revoked_at = grant.revoked_at

    def add_reference(self, reference: SharedArtifactReference) -> None:
        version_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(reference.version_id)
            )
        )
        grant_key = self._session.scalar(
            select(ArtifactSharingGrantRow.id).where(
                ArtifactSharingGrantRow.public_id == str(reference.grant_id)
            )
        )
        project_key = self._project_key(reference.consuming_project_id)
        actor_key = self._principal_key(reference.created_by)
        run_key = (
            self._session.scalar(
                select(RunRow.id).where(
                    RunRow.public_id == str(reference.run_id), RunRow.project_id == project_key
                )
            )
            if reference.run_id is not None
            else None
        )
        if None in {version_key, grant_key, project_key, actor_key}:
            raise ValueError("Shared Artifact Reference refers to a missing resource")
        if reference.run_id is not None and run_key is None:
            raise ValueError("Shared Artifact Reference Run does not exist in consuming project")
        self._session.add(
            SharedArtifactReferenceRow(
                id=uuid4(),
                public_id=str(reference.id),
                artifact_version_id=version_key,
                grant_id=grant_key,
                consuming_project_id=project_key,
                created_by=actor_key,
                run_id=run_key,
                created_at=reference.created_at,
            )
        )

    def references_for_project(
        self, project_id: PublicId
    ) -> tuple[SharedArtifactReference, ...]:
        project_key = self._project_key(project_id)
        if project_key is None:
            return ()
        rows = self._session.execute(
            select(
                SharedArtifactReferenceRow,
                ArtifactVersionRow.public_id,
                ArtifactSharingGrantRow.public_id,
                PrincipalRow.public_id,
                RunRow.public_id,
            )
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == SharedArtifactReferenceRow.artifact_version_id,
            )
            .join(
                ArtifactSharingGrantRow,
                ArtifactSharingGrantRow.id == SharedArtifactReferenceRow.grant_id,
            )
            .join(PrincipalRow, PrincipalRow.id == SharedArtifactReferenceRow.created_by)
            .outerjoin(RunRow, RunRow.id == SharedArtifactReferenceRow.run_id)
            .where(SharedArtifactReferenceRow.consuming_project_id == project_key)
            .order_by(
                SharedArtifactReferenceRow.created_at,
                SharedArtifactReferenceRow.public_id,
            )
        )
        return tuple(
            SharedArtifactReference(
                PublicId(ResourceKind.SHARED_REFERENCE, row.public_id),
                PublicId(ResourceKind.ARTIFACT_VERSION, version_id),
                PublicId(ResourceKind.SHARING_GRANT, grant_id),
                project_id,
                PublicId(ResourceKind.PRINCIPAL, actor_id),
                row.created_at,
                PublicId(ResourceKind.RUN, run_id) if run_id is not None else None,
            )
            for row, version_id, grant_id, actor_id, run_id in rows
        )

    def version_accessible(self, version_id: PublicId, principal_id: PublicId) -> bool:
        return SqlAlchemyArtifactCatalogUnitOfWork(self._session).version_accessible(
            version_id, principal_id
        )

    def derivation_for_derived(self, version_id: PublicId) -> ArtifactDerivation | None:
        row = self._session.scalar(
            select(ArtifactDerivationRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactDerivationRow.derived_version_id,
            )
            .where(ArtifactVersionRow.public_id == str(version_id))
        )
        if row is None:
            return None
        return self._derivation(row)

    def add_derivation(self, derivation: ArtifactDerivation) -> None:
        source_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(derivation.source_version_id)
            )
        )
        derived_key = self._session.scalar(
            select(ArtifactVersionRow.id).where(
                ArtifactVersionRow.public_id == str(derivation.derived_version_id)
            )
        )
        actor_key = self._principal_key(derivation.created_by)
        if None in {source_key, derived_key, actor_key}:
            raise ValueError("Artifact Derivation refers to a missing resource")
        self._session.add(
            ArtifactDerivationRow(
                id=uuid4(),
                public_id=str(derivation.id),
                source_version_id=source_key,
                derived_version_id=derived_key,
                created_by=actor_key,
                created_at=derivation.created_at,
            )
        )

    def _derivation(self, row: ArtifactDerivationRow) -> ArtifactDerivation:
        source_id = self._session.scalar(
            select(ArtifactVersionRow.public_id).where(
                ArtifactVersionRow.id == row.source_version_id
            )
        )
        derived_id = self._session.scalar(
            select(ArtifactVersionRow.public_id).where(
                ArtifactVersionRow.id == row.derived_version_id
            )
        )
        actor_id = self._session.scalar(
            select(PrincipalRow.public_id).where(PrincipalRow.id == row.created_by)
        )
        if None in {source_id, derived_id, actor_id}:
            raise RuntimeError("Artifact Derivation refers to missing canonical resources")
        return ArtifactDerivation(
            PublicId(ResourceKind.DERIVATION, row.public_id),
            PublicId(ResourceKind.ARTIFACT_VERSION, cast(str, source_id)),
            PublicId(ResourceKind.ARTIFACT_VERSION, cast(str, derived_id)),
            PublicId(ResourceKind.PRINCIPAL, cast(str, actor_id)),
            row.created_at,
        )

    def _grant(self, row: ArtifactSharingGrantRow) -> ArtifactSharingGrant:
        version_id, owner_id, consumer_id, actor_id = self._session.execute(
            select(
                ArtifactVersionRow.public_id,
                ResearchProjectRow.public_id,
                ResearchProjectRow.public_id,
                PrincipalRow.public_id,
            )
            .select_from(ArtifactSharingGrantRow)
            .join(ArtifactVersionRow, ArtifactVersionRow.id == row.artifact_version_id)
            .join(PrincipalRow, PrincipalRow.id == row.created_by)
            .join(ResearchProjectRow, ResearchProjectRow.id == row.owning_project_id)
            .where(ArtifactSharingGrantRow.id == row.id)
        ).one()
        consumer_id = self._session.scalar(
            select(ResearchProjectRow.public_id).where(
                ResearchProjectRow.id == row.consuming_project_id
            )
        )
        if consumer_id is None:
            raise RuntimeError("Sharing Grant refers to a missing consuming project")
        return ArtifactSharingGrant(
            PublicId(ResourceKind.SHARING_GRANT, row.public_id),
            PublicId(ResourceKind.ARTIFACT_VERSION, version_id),
            PublicId(ResourceKind.PROJECT, owner_id),
            PublicId(ResourceKind.PROJECT, consumer_id),
            _utc(row.created_at),
            _utc(row.effective_at),
            PublicId(ResourceKind.PRINCIPAL, actor_id),
            _utc(row.revoked_at) if row.revoked_at is not None else None,
        )


class SqlAlchemySecretContextUnitOfWork(SqlAlchemyRepositoryUnitOfWork):
    def secret_context(self, project_id: PublicId) -> SecretContext | None:
        project_key = self._project_key(project_id)
        row = self._session.get(SecretContextRow, project_key) if project_key else None
        if row is None:
            return None
        return SecretContext(
            project_id,
            row.infisical_project_id,
            row.environment_slug,
            row.secret_path,
            row.updated_at,
            row.reconciliation_state,
            row.last_error_code,
        )

    def save_secret_context(self, context: SecretContext) -> None:
        project_key = self._project_key(context.project_id)
        if project_key is None:
            raise ValueError("Secret Context project does not exist")
        row = self._session.get(SecretContextRow, project_key)
        if row is None:
            self._session.add(
                SecretContextRow(
                    project_id=project_key,
                    infisical_project_id=context.infisical_project_id,
                    environment_slug=context.environment_slug,
                    secret_path=context.secret_path,
                    updated_at=context.updated_at,
                    reconciliation_state=context.reconciliation_state,
                    last_error_code=context.last_error_code,
                    last_reconciled_at=None,
                    reconcile_attempt=0,
                )
            )
        else:
            row.infisical_project_id = context.infisical_project_id
            row.environment_slug = context.environment_slug
            row.secret_path = context.secret_path
            row.updated_at = context.updated_at
            row.reconciliation_state = context.reconciliation_state
            row.last_error_code = context.last_error_code
            row.last_reconciled_at = None


class SqlAlchemyMachineCredentialStore(SqlAlchemyRepositoryUnitOfWork):
    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def add_machine_credential(
        self,
        principal: Principal,
        membership: ProjectMembership,
        credential_id: PublicId,
        digest: str,
        scopes: frozenset[MachineScope],
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        project_key = self._project_key(membership.project_id)
        project = self._session.get(ResearchProjectRow, project_key) if project_key else None
        if project is None:
            raise ValueError("machine credential project does not exist")
        principal_key = uuid4()
        self._session.add(
            PrincipalRow(
                id=principal_key,
                public_id=str(principal.id),
                kind=principal.kind.value,
                display_name=principal.display_name,
                created_at=principal.created_at,
                archived_at=None,
            )
        )
        self._session.add(
            OrganizationMembershipRow(
                organization_id=project.organization_id,
                principal_id=principal_key,
                role=OrganizationRole.MEMBER.value,
                created_at=created_at,
            )
        )
        self._session.add(
            ProjectMembershipRow(
                project_id=project.id,
                principal_id=principal_key,
                role=membership.role.value,
                created_at=membership.created_at,
            )
        )
        self._session.add(
            MachineCredentialRow(
                id=uuid4(),
                public_id=str(credential_id),
                principal_id=principal_key,
                project_id=project.id,
                digest=digest,
                scopes=sorted(scope.value for scope in scopes),
                created_at=created_at,
                expires_at=expires_at,
                revoked_at=None,
            )
        )

    def machine_credential(self, credential_id: PublicId) -> StoredMachineCredential | None:
        row = self._session.scalar(
            select(MachineCredentialRow).where(MachineCredentialRow.public_id == str(credential_id))
        )
        if row is None:
            return None
        principal_id, project_id = self._session.execute(
            select(PrincipalRow.public_id, ResearchProjectRow.public_id)
            .select_from(MachineCredentialRow)
            .join(PrincipalRow, PrincipalRow.id == row.principal_id)
            .join(ResearchProjectRow, ResearchProjectRow.id == row.project_id)
            .where(MachineCredentialRow.id == row.id)
        ).one()
        return StoredMachineCredential(
            PublicId(ResourceKind.MACHINE_CREDENTIAL, row.public_id),
            PublicId(ResourceKind.PRINCIPAL, principal_id),
            PublicId(ResourceKind.PROJECT, project_id),
            row.digest,
            frozenset(MachineScope(value) for value in row.scopes),
            _utc(row.expires_at),
            row.revoked_at is not None,
        )

    def machine_credentials(self, project_id: PublicId) -> tuple[StoredMachineCredential, ...]:
        project_key = self._project_key(project_id)
        if project_key is None:
            return ()
        rows = self._session.scalars(
            select(MachineCredentialRow)
            .where(MachineCredentialRow.project_id == project_key)
            .order_by(MachineCredentialRow.created_at, MachineCredentialRow.public_id)
        )
        return tuple(
            StoredMachineCredential(
                PublicId(ResourceKind.MACHINE_CREDENTIAL, row.public_id),
                PublicId(
                    ResourceKind.PRINCIPAL,
                    cast(
                        str,
                        self._session.scalar(
                            select(PrincipalRow.public_id).where(
                                PrincipalRow.id == row.principal_id
                            )
                        ),
                    ),
                ),
                project_id,
                row.digest,
                frozenset(MachineScope(scope) for scope in row.scopes),
                _utc(row.expires_at),
                row.revoked_at is not None,
            )
            for row in rows
        )

    def revoke_machine_credential(self, credential_id: PublicId, revoked_at: datetime) -> None:
        row = self._session.scalar(
            select(MachineCredentialRow).where(
                MachineCredentialRow.public_id == str(credential_id)
            )
        )
        if row is None:
            raise ValueError("machine credential does not exist")
        row.revoked_at = revoked_at


class SqlAlchemyRefreshCredentialStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, credential: NewRefreshCredential) -> None:
        principal_key = self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(credential.principal_id))
        )
        if principal_key is None:
            raise ValueError("principal does not exist")
        self._session.add(
            HumanRefreshCredentialRow(
                id=uuid4(),
                digest=credential.digest,
                family_id=credential.family_id,
                principal_id=principal_key,
                sequence=credential.sequence,
                issued_at=credential.issued_at,
                expires_at=credential.expires_at,
                used_at=None,
                revoked_at=None,
            )
        )
        self._session.commit()

    def rotate(
        self,
        presented_digest: str,
        replacement_digest: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RotationResult:
        row = self._session.scalar(
            select(HumanRefreshCredentialRow)
            .where(HumanRefreshCredentialRow.digest == presented_digest)
            .with_for_update()
        )
        if row is None:
            return RotationResult(RotationStatus.NOT_FOUND)
        if row.used_at is not None:
            return RotationResult(RotationStatus.REUSED, family_id=row.family_id)
        if row.revoked_at is not None:
            return RotationResult(RotationStatus.REVOKED, family_id=row.family_id)
        stored_expiry = row.expires_at
        if stored_expiry.tzinfo is None:
            stored_expiry = stored_expiry.replace(tzinfo=UTC)
        if stored_expiry <= issued_at:
            return RotationResult(RotationStatus.EXPIRED, family_id=row.family_id)
        principal_public_id = self._session.scalar(
            select(PrincipalRow.public_id).where(PrincipalRow.id == row.principal_id)
        )
        if principal_public_id is None:
            raise RuntimeError("refresh credential refers to a missing principal")
        row.used_at = issued_at
        self._session.add(
            HumanRefreshCredentialRow(
                id=uuid4(),
                digest=replacement_digest,
                family_id=row.family_id,
                principal_id=row.principal_id,
                sequence=row.sequence + 1,
                issued_at=issued_at,
                expires_at=expires_at,
                used_at=None,
                revoked_at=None,
            )
        )
        self._session.commit()
        return RotationResult(
            RotationStatus.ROTATED,
            family_id=row.family_id,
            principal_id=PublicId(ResourceKind.PRINCIPAL, principal_public_id),
            sequence=row.sequence + 1,
        )

    def revoke_family(self, family_id: UUID, now: datetime) -> None:
        rows = self._session.scalars(
            select(HumanRefreshCredentialRow).where(
                HumanRefreshCredentialRow.family_id == family_id,
                HumanRefreshCredentialRow.revoked_at.is_(None),
            )
        )
        for row in rows:
            row.revoked_at = now
        self._session.commit()

    def revoke_all(self, principal_id: PublicId, now: datetime) -> None:
        principal_key = self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(principal_id))
        )
        if principal_key is None:
            return
        rows = self._session.scalars(
            select(HumanRefreshCredentialRow).where(
                HumanRefreshCredentialRow.principal_id == principal_key,
                HumanRefreshCredentialRow.revoked_at.is_(None),
            )
        )
        for row in rows:
            row.revoked_at = now
        self._session.commit()

    def family_for_digest(self, digest: str) -> UUID | None:
        return self._session.scalar(
            select(HumanRefreshCredentialRow.family_id).where(
                HumanRefreshCredentialRow.digest == digest
            )
        )

    def principal_for_digest(self, digest: str) -> PublicId | None:
        value = self._session.scalar(
            select(PrincipalRow.public_id)
            .join(
                HumanRefreshCredentialRow,
                HumanRefreshCredentialRow.principal_id == PrincipalRow.id,
            )
            .where(HumanRefreshCredentialRow.digest == digest)
        )
        return PublicId(ResourceKind.PRINCIPAL, value) if value is not None else None


class SqlAlchemyPublicationUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        project_key = self._session.scalar(
            select(ResearchProjectRow.id).where(ResearchProjectRow.public_id == str(project_id))
        )
        principal_key = self._session.scalar(
            select(PrincipalRow.id).where(PrincipalRow.public_id == str(principal_id))
        )
        if (
            project_key is None
            or principal_key is None
            or not _project_accepts_access(self._session, project_key)
        ):
            return None
        role = self._session.scalar(
            select(ProjectMembershipRow.role).where(
                ProjectMembershipRow.project_id == project_key,
                ProjectMembershipRow.principal_id == principal_key,
            )
        )
        return ProjectRole(role) if role is not None else None

    def append_audit(self, event: AuditEvent) -> None:
        SqlAlchemyProjectUnitOfWork(self._session).append_audit(event)

    def find_by_idempotency_key(
        self, project_id: PublicId, key: str
    ) -> PublicationOperation | None:
        project_key = self._session.scalar(
            select(ResearchProjectRow.id).where(ResearchProjectRow.public_id == str(project_id))
        )
        if project_key is None:
            return None
        row = self._session.scalar(
            select(PublicationOperationRow).where(
                PublicationOperationRow.project_id == project_key,
                PublicationOperationRow.idempotency_key == key,
            )
        )
        if row is None:
            return None
        return self._operation(row, project_id)

    def operation(self, operation_id: PublicId) -> PublicationOperation | None:
        row = self._session.scalar(
            select(PublicationOperationRow).where(
                PublicationOperationRow.public_id == str(operation_id)
            )
        )
        if row is None:
            return None
        project_public_id = self._session.scalar(
            select(ResearchProjectRow.public_id).where(ResearchProjectRow.id == row.project_id)
        )
        if project_public_id is None:
            raise RuntimeError("publication operation refers to a missing project")
        return self._operation(row, PublicId(ResourceKind.PROJECT, project_public_id))

    def _operation(
        self, row: PublicationOperationRow, project_id: PublicId
    ) -> PublicationOperation:
        creator_id = (
            self._session.scalar(
                select(PrincipalRow.public_id).where(PrincipalRow.id == row.created_by)
            )
            if row.created_by is not None
            else None
        )
        return PublicationOperation(
            id=PublicId(ResourceKind.PUBLICATION, row.public_id),
            project_id=project_id,
            idempotency_key=row.idempotency_key,
            request_digest=row.request_digest,
            request_payload=row.request_payload,
            state=PublicationState(row.state),
            created_at=row.created_at,
            created_by=(
                PublicId(ResourceKind.PRINCIPAL, creator_id) if creator_id is not None else None
            ),
        )

    def add_operation(self, operation: PublicationOperation) -> None:
        project_key = self._session.scalar(
            select(ResearchProjectRow.id).where(
                ResearchProjectRow.public_id == str(operation.project_id)
            )
        )
        if project_key is None:
            raise ValueError("publication project does not exist")
        creator_key = (
            self._session.scalar(
                select(PrincipalRow.id).where(
                    PrincipalRow.public_id == str(operation.created_by)
                )
            )
            if operation.created_by is not None
            else None
        )
        self._session.add(
            PublicationOperationRow(
                id=uuid4(),
                public_id=str(operation.id),
                project_id=project_key,
                created_by=creator_key,
                idempotency_key=operation.idempotency_key,
                request_digest=operation.request_digest,
                request_payload=operation.request_payload,
                state=operation.state.value,
                created_at=operation.created_at,
                updated_at=operation.created_at,
                claimed_at=None,
                claimed_by=None,
                attempt=0,
                events_expired_through=0,
            )
        )
        self._session.flush()

    def add_event(self, event: PublicationEvent) -> None:
        operation_key = self._session.scalar(
            select(PublicationOperationRow.id).where(
                PublicationOperationRow.public_id == str(event.operation_id)
            )
        )
        if operation_key is None:
            raise ValueError("publication operation does not exist")
        self._session.add(
            PublicationEventRow(
                operation_id=operation_key,
                sequence=event.sequence,
                event_name=event.name,
                occurred_at=event.occurred_at,
                payload=event.payload,
            )
        )

    def events_after(self, operation_id: PublicId, sequence: int) -> tuple[PublicationEvent, ...]:
        operation_key = self._session.scalar(
            select(PublicationOperationRow.id).where(
                PublicationOperationRow.public_id == str(operation_id)
            )
        )
        if operation_key is None:
            return ()
        rows = self._session.scalars(
            select(PublicationEventRow)
            .where(
                PublicationEventRow.operation_id == operation_key,
                PublicationEventRow.sequence > sequence,
            )
            .order_by(PublicationEventRow.sequence)
        )
        return tuple(
            PublicationEvent(
                operation_id, row.sequence, row.event_name, row.occurred_at, row.payload
            )
            for row in rows
        )

    def event_history_expired_through(self, operation_id: PublicId) -> int:
        value = self._session.scalar(
            select(PublicationOperationRow.events_expired_through).where(
                PublicationOperationRow.public_id == str(operation_id)
            )
        )
        return int(value or 0)

    def prune_events(self, before: datetime) -> int:
        operation_keys = self._session.scalars(
            select(PublicationEventRow.operation_id)
            .where(PublicationEventRow.occurred_at < before)
            .distinct()
        ).all()
        removed = 0
        for operation_key in operation_keys:
            maximum = self._session.scalar(
                select(func.max(PublicationEventRow.sequence)).where(
                    PublicationEventRow.operation_id == operation_key,
                    PublicationEventRow.occurred_at < before,
                )
            )
            row = self._session.get(PublicationOperationRow, operation_key)
            if row is not None and maximum is not None:
                row.events_expired_through = max(row.events_expired_through, int(maximum))
            count = self._session.scalar(
                select(func.count()).where(
                    PublicationEventRow.operation_id == operation_key,
                    PublicationEventRow.occurred_at < before,
                )
            )
            self._session.execute(
                delete(PublicationEventRow).where(
                    PublicationEventRow.operation_id == operation_key,
                    PublicationEventRow.occurred_at < before,
                )
            )
            removed += int(count or 0)
        self._session.commit()
        return removed

    def commit(self) -> None:
        self._session.commit()


class SqlAlchemyPublicationWorkStore(SqlAlchemyPublicationUnitOfWork):
    def __init__(self, session: Session, claim_timeout: timedelta = timedelta(minutes=35)) -> None:
        super().__init__(session)
        self._claim_timeout = claim_timeout

    def claim_next(self, worker_id: str, now: datetime) -> PublicationOperation | None:
        reclaim_before = now - self._claim_timeout
        row = self._session.scalar(
            select(PublicationOperationRow)
            .where(
                (
                    (PublicationOperationRow.state == PublicationState.QUEUED.value)
                    & (PublicationOperationRow.claimed_at.is_(None))
                )
                | (
                    PublicationOperationRow.state.in_(
                        (
                            PublicationState.RESOLVING.value,
                            PublicationState.VERIFYING.value,
                            PublicationState.COMMITTING.value,
                        )
                    )
                    & (PublicationOperationRow.claimed_at < reclaim_before)
                ),
            )
            .order_by(PublicationOperationRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        if row.state != PublicationState.QUEUED.value:
            row.state = PublicationState.QUEUED.value
            self._add_next_event(
                row.id,
                PublicId(ResourceKind.PUBLICATION, row.public_id),
                "operation.queued",
                now,
                {"reason": "stale_claim_recovered"},
            )
        row.claimed_at = now
        row.claimed_by = worker_id
        row.attempt += 1
        self._session.commit()
        project_public_id = self._session.scalar(
            select(ResearchProjectRow.public_id).where(ResearchProjectRow.id == row.project_id)
        )
        if project_public_id is None:
            raise RuntimeError("publication operation refers to a missing project")
        return self._operation(row, PublicId(ResourceKind.PROJECT, project_public_id))

    def advance(
        self,
        operation: PublicationOperation,
        target: PublicationState,
        event_name: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> PublicationOperation:
        updated = operation.transition(target)
        row = self._required_row(operation.id)
        row.state = target.value
        row.updated_at = now
        self._add_next_event(row.id, operation.id, event_name, now, payload or {})
        self._session.commit()
        return updated

    def publish(
        self, operation: PublicationOperation, validated: ValidatedPublication, now: datetime
    ) -> ArtifactVersion:
        published = operation.transition(PublicationState.PUBLISHED)
        row = self._required_row(operation.id)
        artifact_row = self._session.scalar(
            select(ArtifactRow).where(
                ArtifactRow.public_id == str(validated.artifact_id),
                ArtifactRow.owning_project_id == row.project_id,
            )
        )
        if artifact_row is None:
            raise ValueError("validated Artifact does not belong to the publication project")
        version = artifact_version_from_validation(operation, validated, now)
        version_key = uuid4()
        producing_run_key = None
        if validated.producing_run_id is not None:
            producing_run_key = self._session.scalar(
                select(RunRow.id).where(
                    RunRow.public_id == str(validated.producing_run_id),
                    RunRow.project_id == row.project_id,
                )
            )
            if producing_run_key is None:
                raise ValueError("validated producing Run does not belong to the project")
        self._session.add(
            ArtifactVersionRow(
                id=version_key,
                public_id=str(version.id),
                artifact_id=artifact_row.id,
                owning_project_id=row.project_id,
                publication_operation_id=row.id,
                producing_run_id=producing_run_key,
                algorithm=version.identity.algorithm,
                digest=version.identity.digest,
                output_kind=version.identity.kind.value,
                size=version.identity.size,
                file_count=version.identity.file_count,
                integrity=version.integrity.value,
                availability=version.availability.value,
                published_at=version.published_at,
            )
        )
        for item in validated.files:
            self._session.add(
                ArtifactVersionFileRow(
                    artifact_version_id=version_key,
                    path=item.path,
                    size=item.size,
                    digest=item.digest,
                )
            )
        self._session.add(
            ArtifactStorageLocationRow(
                artifact_version_id=version_key,
                bucket=validated.bucket,
                object_key=validated.object_key,
                created_at=now,
            )
        )
        if producing_run_key is not None and row.created_by is not None:
            source_versions = tuple(
                self._session.scalars(
                    select(RunArtifactInputRow.artifact_version_id).where(
                        RunArtifactInputRow.run_id == producing_run_key
                    )
                )
            )
            if len(source_versions) == 1:
                self._session.add(
                    ArtifactDerivationRow(
                        id=uuid4(),
                        public_id=str(PublicId.generate(ResourceKind.DERIVATION)),
                        source_version_id=source_versions[0],
                        derived_version_id=version_key,
                        created_by=row.created_by,
                        created_at=now,
                    )
                )
        row.state = published.state.value
        row.updated_at = now
        row.artifact_version_id = version_key
        row.claimed_at = None
        row.claimed_by = None
        self._add_next_event(
            row.id,
            operation.id,
            "operation.published",
            now,
            {"artifact_version_id": str(version.id)},
        )
        self._audit_terminal(row, now, "success", {"artifact_version_id": str(version.id)})
        self._session.commit()
        return version

    def fail(
        self, operation: PublicationOperation, failure_code: str, now: datetime
    ) -> PublicationOperation:
        self._session.rollback()
        failed = operation.transition(PublicationState.FAILED)
        row = self._required_row(operation.id)
        row.state = failed.state.value
        row.failure_code = failure_code
        row.updated_at = now
        row.claimed_at = None
        row.claimed_by = None
        self._add_next_event(
            row.id,
            operation.id,
            "operation.failed",
            now,
            {"failure_code": failure_code},
        )
        self._audit_terminal(row, now, "failed", {"failure_code": failure_code})
        self._session.commit()
        return failed

    def _required_row(self, operation_id: PublicId) -> PublicationOperationRow:
        row = self._session.scalar(
            select(PublicationOperationRow).where(
                PublicationOperationRow.public_id == str(operation_id)
            )
        )
        if row is None:
            raise ValueError("publication operation does not exist")
        return row

    def _audit_terminal(
        self,
        row: PublicationOperationRow,
        now: datetime,
        outcome: str,
        metadata: dict[str, str],
    ) -> None:
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_principal_id=row.created_by,
                project_id=row.project_id,
                action="publication.complete",
                resource_type="publication_operation",
                resource_id=row.public_id,
                outcome=outcome,
                request_id=str(PublicId.generate(ResourceKind.REQUEST)),
                safe_metadata=metadata,
            )
        )

    def _add_next_event(
        self,
        operation_key: UUID,
        operation_id: PublicId,
        name: str,
        now: datetime,
        payload: dict[str, Any],
    ) -> None:
        sequence = self._session.scalar(
            select(func.coalesce(func.max(PublicationEventRow.sequence), 0)).where(
                PublicationEventRow.operation_id == operation_key
            )
        )
        self.add_event(PublicationEvent(operation_id, int(sequence or 0) + 1, name, now, payload))


@lru_cache
def database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_session(database_url: str) -> Session:
    return Session(database_engine(database_url))


def database_is_ready(database_url: str) -> bool:
    try:
        with database_engine(database_url).connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
