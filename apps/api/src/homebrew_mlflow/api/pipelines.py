from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from homebrew_mlflow.application import AccessTokenClaims, PipelineService
from homebrew_mlflow.domain import PipelineDefinition, PipelineVersion, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    GitLabPipelineSourceReader,
    SqlAlchemyPipelineUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["pipelines"])


class CreatePipelineDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class RegisterPipelineVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str
    git_commit_sha: str = Field(pattern="^[0-9a-f]{40}$")
    pipeline_path: str = Field(min_length=1, max_length=1000)


class PipelineDefinitionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    created_at: datetime
    archived_at: datetime | None


class PipelineVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    definition_id: str
    repository_id: str
    git_commit_sha: str
    pipeline_path: str
    content_sha256: str
    created_at: datetime
    archived_at: datetime | None


def _id(kind: ResourceKind, value: str, detail: str) -> PublicId:
    try:
        return PublicId(kind, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=detail) from error


def _definition(value: PipelineDefinition) -> PipelineDefinitionResponse:
    return PipelineDefinitionResponse(
        id=str(value.id),
        project_id=str(value.project_id),
        name=value.name,
        created_at=value.created_at,
        archived_at=value.archived_at,
    )


def _version(value: PipelineVersion) -> PipelineVersionResponse:
    return PipelineVersionResponse(
        id=str(value.id),
        definition_id=str(value.definition_id),
        repository_id=str(value.repository_id),
        git_commit_sha=value.git_commit_sha,
        pipeline_path=value.pipeline_path,
        content_sha256=value.content_sha256,
        created_at=value.created_at,
        archived_at=value.archived_at,
    )


@router.get(
    "/projects/{project_id}/pipeline-definitions",
    response_model=list[PipelineDefinitionResponse],
)
def list_pipeline_definitions(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[PipelineDefinitionResponse]:
    parsed = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    with create_session(get_settings().database_url) as session:
        values = PipelineService(SqlAlchemyPipelineUnitOfWork(session)).list_definitions(
            claims.principal_id, parsed, include_archived=include_archived
        )
    return [_definition(value) for value in values]


@router.post(
    "/projects/{project_id}/pipeline-definitions",
    response_model=PipelineDefinitionResponse,
)
def create_pipeline_definition(
    project_id: str,
    body: CreatePipelineDefinitionRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> PipelineDefinitionResponse:
    parsed = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    now = datetime.now(UTC)
    with create_session(get_settings().database_url) as session:
        value = PipelineService(SqlAlchemyPipelineUnitOfWork(session)).create_definition(
            claims.principal_id,
            parsed,
            body.name,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            now,
        )
    return _definition(value)


@router.get(
    "/pipeline-definitions/{definition_id}/versions",
    response_model=list[PipelineVersionResponse],
)
def list_pipeline_versions(
    definition_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[PipelineVersionResponse]:
    parsed = _id(
        ResourceKind.PIPELINE_DEFINITION, definition_id, "pipeline_definition_not_found"
    )
    with create_session(get_settings().database_url) as session:
        values = PipelineService(SqlAlchemyPipelineUnitOfWork(session)).list_versions(
            claims.principal_id, parsed, include_archived=include_archived
        )
    return [_version(value) for value in values]


@router.post(
    "/pipeline-definitions/{definition_id}/versions",
    response_model=PipelineVersionResponse,
)
def register_pipeline_version(
    definition_id: str,
    body: RegisterPipelineVersionRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> PipelineVersionResponse:
    definition = _id(
        ResourceKind.PIPELINE_DEFINITION, definition_id, "pipeline_definition_not_found"
    )
    repository = _id(ResourceKind.REPOSITORY, body.repository_id, "repository_not_found")
    settings = get_settings()
    now = datetime.now(UTC)
    with create_session(settings.database_url) as session:
        value = PipelineService(
            SqlAlchemyPipelineUnitOfWork(session),
            GitLabPipelineSourceReader(
                session,
                str(settings.gitlab_base_url),
                settings.gitlab_integration_token.get_secret_value(),
            ),
        ).register_version(
            claims.principal_id,
            definition,
            repository,
            body.git_commit_sha,
            body.pipeline_path,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            now,
        )
    return _version(value)


@router.delete(
    "/pipeline-definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_pipeline_definition(
    definition_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    parsed = _id(
        ResourceKind.PIPELINE_DEFINITION, definition_id, "pipeline_definition_not_found"
    )
    with create_session(get_settings().database_url) as session:
        PipelineService(SqlAlchemyPipelineUnitOfWork(session)).archive_definition(
            claims.principal_id,
            parsed,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/pipeline-definitions/{definition_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_pipeline_version(
    definition_id: str,
    version_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    definition = _id(
        ResourceKind.PIPELINE_DEFINITION, definition_id, "pipeline_definition_not_found"
    )
    version = _id(ResourceKind.PIPELINE_VERSION, version_id, "pipeline_version_not_found")
    with create_session(get_settings().database_url) as session:
        PipelineService(SqlAlchemyPipelineUnitOfWork(session)).archive_version(
            claims.principal_id,
            definition,
            version,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
