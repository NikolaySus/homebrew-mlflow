from __future__ import annotations

import shlex
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from homebrew_mlflow.application import AccessTokenClaims, ArtifactCatalogService
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyArtifactCatalogUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(tags=["artifacts"])


class CreateArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    created_at: datetime
    archived_at: datetime | None


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


class RetentionDependenciesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retained_runs: int
    shared_references: int
    derivatives: int
    active_grants: int
    replicas: int
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
            claims.principal_id, parsed_project, body.name, datetime.now(UTC)
        )
    return ArtifactResponse(
        id=str(artifact.id),
        project_id=str(artifact.owning_project_id),
        name=artifact.name,
        created_at=artifact.created_at,
        archived_at=artifact.archived_at,
    )


@router.get("/api/v1/projects/{project_id}/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ArtifactResponse]:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        artifacts = ArtifactCatalogService(
            SqlAlchemyArtifactCatalogUnitOfWork(session)
        ).list_artifacts(claims.principal_id, parsed_project)
    return [
        ArtifactResponse(
            id=str(artifact.id),
            project_id=str(artifact.owning_project_id),
            name=artifact.name,
            created_at=artifact.created_at,
            archived_at=artifact.archived_at,
        )
        for artifact in artifacts
    ]


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


def _optional_run(value: str | None) -> PublicId | None:
    if value is None:
        return None
    try:
        return PublicId(ResourceKind.RUN, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
