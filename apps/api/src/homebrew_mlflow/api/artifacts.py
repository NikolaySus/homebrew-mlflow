from __future__ import annotations

import shlex
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from homebrew_mlflow.application import (
    AccessTokenClaims,
    ArtifactArchiveCapacityUnavailable,
    ArtifactArchiveJob,
    ArtifactArchiveNotReady,
    ArtifactArchiveService,
    ArtifactArchiveState,
    ArtifactArchiveTooLarge,
    ArtifactCatalogService,
)
from homebrew_mlflow.domain import (
    ArtifactKind,
    PublicId,
    ResourceKind,
    normalize_artifact_alias,
)
from homebrew_mlflow.infrastructure import (
    S3ArtifactArchiveBuilder,
    SqlAlchemyArtifactArchiveStore,
    SqlAlchemyArtifactCatalogUnitOfWork,
    archive_filename,
    create_session,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .security import platform_claims
from .settings import get_settings

router = APIRouter(tags=["artifacts"])


class CreateArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: ArtifactKind = ArtifactKind.GENERIC
    description: str | None = Field(default=None, max_length=2000)


class UpdateArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    description: str | None = Field(default=None, max_length=2000)


class SetArtifactAliasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version_id: str


class ArtifactAliasResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    artifact_version_id: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    kind: ArtifactKind
    description: str | None
    created_at: datetime
    archived_at: datetime | None


class ArtifactSummaryResponse(ArtifactResponse):
    active_version_count: int
    latest_version_sequence: int | None
    latest_version_published_at: datetime | None


class ArtifactVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    artifact_id: str
    owning_project_id: str
    algorithm: str
    digest: str
    output_kind: str
    size: int
    file_count: int
    integrity: str
    availability: str
    published_at: datetime
    archived_at: datetime | None
    sequence: int
    mlflow_model_id: str
    producing_run_id: str | None
    model_signature: dict[str, object] | None
    model_signature_sha256: str | None


class RetentionDependenciesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retained_runs: int
    shared_references: int
    derivatives: int
    active_grants: int
    replicas: int
    aliases: int
    legal_hold: bool
    blockers: list[str]


class ArtifactFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    digest: str | None


class ArtifactLineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_artifact_version_id: str
    derived_artifact_version_id: str
    created_at: datetime


class ArtifactConsumptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version_id: str
    pointer_filename: str
    dvc_remote_name: str
    dvc_remote_url: str
    s3_endpoint_url: str
    bash_commands: list[str]
    powershell_commands: list[str]


class ArtifactArchiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str
    state: ArtifactArchiveState
    processed_bytes: int
    total_bytes: int
    archive_size: int | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    failure_code: str | None


class ArtifactArchiveUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_url: str
    expires_at: datetime
    filename: str


def _archive_response(job: ArtifactArchiveJob) -> ArtifactArchiveResponse:
    return ArtifactArchiveResponse(
        version_id=str(job.version_id), state=job.state,
        processed_bytes=job.processed_bytes, total_bytes=job.total_bytes,
        archive_size=job.archive_size, created_at=job.created_at,
        updated_at=job.updated_at, expires_at=job.expires_at,
        failure_code=job.failure_code,
    )


def _archive_service(session: Session) -> ArtifactArchiveService:
    settings = get_settings()
    return ArtifactArchiveService(
        SqlAlchemyArtifactArchiveStore(session),
        max_bytes=settings.artifact_archive_max_bytes,
        max_files=settings.artifact_archive_max_files,
        cache_bytes=settings.artifact_archive_cache_bytes,
    )


def _archive_error(error: Exception) -> HTTPException:
    if isinstance(error, ArtifactArchiveTooLarge):
        return HTTPException(status_code=413, detail=str(error))
    if isinstance(error, (ArtifactArchiveCapacityUnavailable, ArtifactArchiveNotReady)):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=404, detail=str(error))


def _version_response(version) -> ArtifactVersionResponse:  # type: ignore[no-untyped-def]
    return ArtifactVersionResponse(
        id=str(version.id),
        artifact_id=str(version.artifact_id),
        owning_project_id=str(version.owning_project_id),
        algorithm=version.identity.algorithm,
        digest=version.identity.digest,
        output_kind=version.identity.kind.value,
        size=version.identity.size,
        file_count=version.identity.file_count,
        integrity=version.integrity.value,
        availability=version.availability.value,
        published_at=version.published_at,
        archived_at=version.archived_at,
        sequence=version.sequence,
        mlflow_model_id=version.mlflow_model_id,
        producing_run_id=(
            str(version.producing_run_id) if version.producing_run_id is not None else None
        ),
        model_signature=version.model_signature,
        model_signature_sha256=version.model_signature_sha256,
    )


@router.post(
    "/api/v1/projects/{project_id}/artifacts",
    response_model=ArtifactResponse,
    status_code=201,
)
def create_artifact(
    project_id: str,
    body: CreateArtifactRequest,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactResponse:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        artifact = ArtifactCatalogService(SqlAlchemyArtifactCatalogUnitOfWork(session)).create(
            claims.principal_id,
            parsed_project,
            body.name,
            datetime.now(UTC),
            body.kind,
            body.description,
        )
    return ArtifactResponse(
        id=str(artifact.id),
        project_id=str(artifact.owning_project_id),
        name=artifact.name,
        kind=artifact.kind,
        description=artifact.description,
        created_at=artifact.created_at,
        archived_at=artifact.archived_at,
    )


@router.get(
    "/api/v1/projects/{project_id}/artifacts",
    response_model=list[ArtifactSummaryResponse],
)
def list_artifacts(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ArtifactSummaryResponse]:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        artifacts = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).list_artifact_summaries(claims.principal_id, parsed_project)
    return [
        ArtifactSummaryResponse(
            id=str(summary.artifact.id),
            project_id=str(summary.artifact.owning_project_id),
            name=summary.artifact.name,
            kind=summary.artifact.kind,
            description=summary.artifact.description,
            created_at=summary.artifact.created_at,
            archived_at=summary.artifact.archived_at,
            active_version_count=summary.active_version_count,
            latest_version_sequence=summary.latest_version_sequence,
            latest_version_published_at=summary.latest_version_published_at,
        )
        for summary in artifacts
    ]


@router.patch("/api/v1/artifacts/{artifact_id}", response_model=ArtifactResponse)
def update_artifact(
    artifact_id: str,
    body: UpdateArtifactRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT, artifact_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_not_found") from error
    with create_session(get_settings().database_url) as session:
        try:
            artifact = ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).update(
                claims.principal_id,
                parsed,
                body.kind,
                body.description,
                PublicId(ResourceKind.REQUEST, request.state.request_id),
                datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact_not_found") from error
    return ArtifactResponse(
        id=str(artifact.id),
        project_id=str(artifact.owning_project_id),
        name=artifact.name,
        kind=artifact.kind,
        description=artifact.description,
        created_at=artifact.created_at,
        archived_at=artifact.archived_at,
    )


def _alias_response(value) -> ArtifactAliasResponse:  # type: ignore[no-untyped-def]
    return ArtifactAliasResponse(
        alias=value.alias,
        artifact_version_id=str(value.artifact_version_id),
        created_by=str(value.created_by),
        created_at=value.created_at,
        updated_by=str(value.updated_by),
        updated_at=value.updated_at,
    )


@router.get(
    "/api/v1/artifacts/{artifact_id}/aliases", response_model=list[ArtifactAliasResponse]
)
def list_artifact_aliases(
    artifact_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ArtifactAliasResponse]:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT, artifact_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_not_found") from error
    with create_session(get_settings().database_url) as session:
        try:
            values = ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).list_aliases(claims.principal_id, parsed)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact_not_found") from error
    return [_alias_response(value) for value in values]


@router.put(
    "/api/v1/artifacts/{artifact_id}/aliases/{alias}",
    response_model=ArtifactAliasResponse,
)
def set_artifact_alias(
    artifact_id: str,
    alias: str,
    body: SetArtifactAliasRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactAliasResponse:
    try:
        parsed_artifact = PublicId(ResourceKind.ARTIFACT, artifact_id)
        parsed_version = PublicId(ResourceKind.ARTIFACT_VERSION, body.artifact_version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_or_version_not_found") from error
    try:
        normalized_alias = normalize_artifact_alias(alias)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_artifact_alias") from error
    with create_session(get_settings().database_url) as session:
        try:
            value = ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).set_alias(
                claims.principal_id,
                parsed_artifact,
                normalized_alias,
                parsed_version,
                PublicId(ResourceKind.REQUEST, request.state.request_id),
                datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(
                status_code=404, detail="artifact_or_version_not_found"
            ) from error
    return _alias_response(value)


@router.delete(
    "/api/v1/artifacts/{artifact_id}/aliases/{alias}", status_code=204
)
def delete_artifact_alias(
    artifact_id: str,
    alias: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT, artifact_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_not_found") from error
    try:
        normalized_alias = normalize_artifact_alias(alias)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_artifact_alias") from error
    with create_session(get_settings().database_url) as session:
        try:
            ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).delete_alias(
                claims.principal_id,
                parsed,
                normalized_alias,
                PublicId(ResourceKind.REQUEST, request.state.request_id),
                datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact_alias_not_found") from error
    return Response(status_code=204)


@router.get(
    "/api/v1/artifacts/{artifact_id}/versions", response_model=list[ArtifactVersionResponse]
)
def list_artifact_versions(
    artifact_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ArtifactVersionResponse]:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT, artifact_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_not_found") from error
    with create_session(get_settings().database_url) as session:
        versions = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).list_versions(claims.principal_id, parsed)
    return [_version_response(version) for version in versions]


@router.get("/api/v1/artifact-versions/{version_id}", response_model=ArtifactVersionResponse)
def get_artifact_version(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    recovery_run_id: Annotated[str | None, Query()] = None,
) -> ArtifactVersionResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    parsed_run = _optional_run(recovery_run_id)
    with create_session(get_settings().database_url) as session:
        version = ArtifactCatalogService(SqlAlchemyArtifactCatalogUnitOfWork(session)).get_version(
            claims.principal_id, parsed, parsed_run
        )
    return _version_response(version)


@router.delete("/api/v1/artifacts/{artifact_id}", response_model=ArtifactResponse)
def archive_artifact(
    artifact_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT, artifact_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_not_found") from error
    with create_session(get_settings().database_url) as session:
        artifact = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).archive_artifact(
            claims.principal_id,
            parsed,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return ArtifactResponse(
        id=str(artifact.id),
        project_id=str(artifact.owning_project_id),
        name=artifact.name,
        kind=artifact.kind,
        description=artifact.description,
        created_at=artifact.created_at,
        archived_at=artifact.archived_at,
    )


@router.delete(
    "/api/v1/artifact-versions/{version_id}", response_model=ArtifactVersionResponse
)
def archive_artifact_version(
    version_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactVersionResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    with create_session(get_settings().database_url) as session:
        version = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).archive_version(
            claims.principal_id,
            parsed,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _version_response(version)


@router.get(
    "/api/v1/artifact-versions/{version_id}/retention-dependencies",
    response_model=RetentionDependenciesResponse,
)
def get_retention_dependencies(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RetentionDependenciesResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    with create_session(get_settings().database_url) as session:
        value = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).dependencies(claims.principal_id, parsed)
    return RetentionDependenciesResponse(
        retained_runs=value.retained_runs,
        shared_references=value.shared_references,
        derivatives=value.derivatives,
        active_grants=value.active_grants,
        replicas=value.replicas,
        aliases=value.aliases,
        legal_hold=value.legal_hold,
        blockers=list(value.blockers),
    )


@router.get(
    "/api/v1/artifact-versions/{version_id}/files",
    response_model=list[ArtifactFileResponse],
)
def list_artifact_version_files(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    recovery_run_id: Annotated[str | None, Query()] = None,
) -> list[ArtifactFileResponse]:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    parsed_run = _optional_run(recovery_run_id)
    with create_session(get_settings().database_url) as session:
        files = ArtifactCatalogService(SqlAlchemyArtifactCatalogUnitOfWork(session)).files(
            claims.principal_id, parsed, parsed_run
        )
    return [
        ArtifactFileResponse(path=item.path, size=item.size, digest=item.digest) for item in files
    ]


@router.get("/api/v1/artifact-versions/{version_id}/pointer")
def download_artifact_version_pointer(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    recovery_run_id: Annotated[str | None, Query()] = None,
) -> Response:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    parsed_run = _optional_run(recovery_run_id)
    with create_session(get_settings().database_url) as session:
        try:
            pointer = ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).pointer(claims.principal_id, parsed, parsed_run)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    filename = pointer.output_path.rsplit("/", 1)[-1] + ".dvc"
    return Response(
        pointer.content(),
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/api/v1/artifact-versions/{version_id}/consumption",
    response_model=ArtifactConsumptionResponse,
)
def get_artifact_version_consumption(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    recovery_run_id: Annotated[str | None, Query()] = None,
) -> ArtifactConsumptionResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    parsed_run = _optional_run(recovery_run_id)
    with create_session(get_settings().database_url) as session:
        try:
            pointer = ArtifactCatalogService(
                SqlAlchemyArtifactCatalogUnitOfWork(session)
            ).pointer(claims.principal_id, parsed, parsed_run)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    settings = get_settings()
    filename = pointer.output_path.rsplit("/", 1)[-1] + ".dvc"
    remote_name = f"shared-{version_id.lower()}"
    remote_url = f"s3://{settings.dvc_bucket}/dvc/{pointer.version.owning_project_id}"
    recovery_bash = f" --recovery-run {recovery_run_id}" if recovery_run_id else ""
    recovery_powershell = recovery_bash
    bash = [
        f"homebrew-mlflow artifact pointer --version {version_id} "
        f"--output {shlex.quote(filename)}{recovery_bash}",
        f"dvc remote add --local {remote_name} {remote_url}",
        f"dvc remote modify --local {remote_name} endpointurl {settings.s3_public_endpoint_url}",
        f"dvc remote modify --local {remote_name} profile homebrew-mlflow",
        f"dvc pull {shlex.quote(filename)} -r {remote_name}",
    ]
    powershell_filename = "'" + filename.replace("'", "''") + "'"
    powershell = [
        f"homebrew-mlflow artifact pointer --version {version_id} "
        f"--output {powershell_filename}{recovery_powershell}",
        f"dvc remote add --local {remote_name} {remote_url}",
        f"dvc remote modify --local {remote_name} endpointurl {settings.s3_public_endpoint_url}",
        f"dvc remote modify --local {remote_name} profile homebrew-mlflow",
        f"dvc pull {powershell_filename} -r {remote_name}",
    ]
    return ArtifactConsumptionResponse(
        artifact_version_id=version_id,
        pointer_filename=filename,
        dvc_remote_name=remote_name,
        dvc_remote_url=remote_url,
        s3_endpoint_url=str(settings.s3_public_endpoint_url),
        bash_commands=bash,
        powershell_commands=powershell,
    )


@router.get(
    "/api/v1/artifact-versions/{version_id}/lineage",
    response_model=list[ArtifactLineageResponse],
)
def get_artifact_version_lineage(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ArtifactLineageResponse]:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="artifact_version_not_found") from error
    with create_session(get_settings().database_url) as session:
        edges = ArtifactCatalogService(SqlAlchemyArtifactCatalogUnitOfWork(session)).lineage(
            claims.principal_id, parsed
        )
    return [
        ArtifactLineageResponse(
            id=str(edge.id),
            source_artifact_version_id=str(edge.source_version_id),
            derived_artifact_version_id=str(edge.derived_version_id),
            created_at=edge.created_at,
        )
        for edge in edges
    ]


@router.post(
    "/api/v1/artifact-versions/{version_id}/archive",
    response_model=ArtifactArchiveResponse,
)
def request_artifact_archive(
    version_id: str,
    response: Response,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactArchiveResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
        with create_session(get_settings().database_url) as session:
            job = _archive_service(session).request(
                claims.principal_id, parsed, datetime.now(UTC)
            )
    except (ValueError, ArtifactArchiveTooLarge, ArtifactArchiveCapacityUnavailable) as error:
        raise _archive_error(error) from error
    if job.state in {ArtifactArchiveState.PENDING, ArtifactArchiveState.BUILDING}:
        response.status_code = 202
    return _archive_response(job)


@router.get(
    "/api/v1/artifact-versions/{version_id}/archive",
    response_model=ArtifactArchiveResponse,
)
def get_artifact_archive(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactArchiveResponse:
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
        with create_session(get_settings().database_url) as session:
            job = _archive_service(session).status(
                claims.principal_id, parsed, datetime.now(UTC)
            )
    except ValueError as error:
        raise _archive_error(error) from error
    return _archive_response(job)


@router.post(
    "/api/v1/artifact-versions/{version_id}/archive-url",
    response_model=ArtifactArchiveUrlResponse,
)
def create_artifact_archive_url(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ArtifactArchiveUrlResponse:
    settings = get_settings()
    now = datetime.now(UTC)
    try:
        parsed = PublicId(ResourceKind.ARTIFACT_VERSION, version_id)
        with create_session(settings.database_url) as session:
            job, source = _archive_service(session).ready(
                claims.principal_id, parsed, now
            )
    except (ValueError, ArtifactArchiveNotReady) as error:
        raise _archive_error(error) from error
    filename = archive_filename(source.artifact_name, source.version.sequence)
    builder = S3ArtifactArchiveBuilder(
        endpoint_url=str(settings.s3_public_endpoint_url),
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        destination_bucket=settings.attachment_bucket,
    )
    return ArtifactArchiveUrlResponse(
        download_url=builder.presigned_download(
            job.object_key or "", filename, settings.artifact_archive_url_seconds
        ),
        expires_at=now + timedelta(seconds=settings.artifact_archive_url_seconds),
        filename=filename,
    )


def _optional_run(value: str | None) -> PublicId | None:
    if value is None:
        return None
    try:
        return PublicId(ResourceKind.RUN, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
