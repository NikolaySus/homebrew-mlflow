from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from homebrew_mlflow.application import (
    AccessTokenClaims,
    CreateProject,
    CreateRepository,
    ProjectService,
    RepositoryService,
)
from homebrew_mlflow.domain import GitRepository, PublicId, ResearchProject, ResourceKind
from homebrew_mlflow.infrastructure import (
    SqlAlchemyProjectUnitOfWork,
    SqlAlchemyRepositoryUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class CreateRepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    default_branch: str = Field(default="main", min_length=1, max_length=100)


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    slug: str
    default_branch: str
    state: str
    web_url: str | None
    http_clone_url: str | None
    ssh_clone_url: str | None
    failure_code: str | None


class ProjectCreationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    name: str
    slug: str
    default_repository: RepositoryResponse


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    name: str
    slug: str
    state: str
    created_at: datetime
    archived_at: datetime | None


def _project_response(project: ResearchProject) -> ProjectResponse:
    return ProjectResponse(
        id=str(project.id),
        organization_id=str(project.organization_id),
        name=project.name,
        slug=project.slug,
        state=project.state.value,
        created_at=project.created_at,
        archived_at=project.archived_at,
    )


def _repository_response(repository: GitRepository) -> RepositoryResponse:
    return RepositoryResponse(
        id=str(repository.id),
        project_id=str(repository.project_id),
        name=repository.name,
        slug=repository.slug,
        default_branch=repository.default_branch,
        state=repository.state.value,
        web_url=repository.web_url,
        http_clone_url=repository.http_clone_url,
        ssh_clone_url=repository.ssh_clone_url,
        failure_code=repository.failure_code,
    )


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ProjectResponse]:
    with create_session(get_settings().database_url) as session:
        projects = ProjectService(SqlAlchemyProjectUnitOfWork(session)).list_for_actor(
            claims.principal_id
        )
    return [_project_response(project) for project in projects]


@router.delete("/{project_id}", response_model=ProjectResponse)
def archive_project(
    project_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ProjectResponse:
    try:
        parsed = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        project = ProjectService(SqlAlchemyProjectUnitOfWork(session)).archive(
            claims.principal_id,
            parsed,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _project_response(project)


@router.post("/{project_id}/restore", response_model=ProjectResponse)
def restore_project(
    project_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ProjectResponse:
    try:
        parsed = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        project = ProjectService(SqlAlchemyProjectUnitOfWork(session)).restore(
            claims.principal_id,
            parsed,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _project_response(project)


@router.post("", response_model=ProjectCreationResponse, status_code=status.HTTP_202_ACCEPTED)
def create_project(
    body: CreateProjectRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ProjectCreationResponse:
    organization_id = PublicId(ResourceKind.ORGANIZATION, body.organization_id)
    request_id = PublicId(ResourceKind.REQUEST, request.state.request_id)
    settings = get_settings()
    now = datetime.now(UTC)
    with create_session(settings.database_url) as session:
        project = ProjectService(SqlAlchemyProjectUnitOfWork(session)).create(
            claims.principal_id,
            CreateProject(
                organization_id=organization_id,
                name=body.name,
                slug=body.slug,
                initial_maintainer_id=claims.principal_id,
                request_id=request_id,
            ),
        )
        repository = RepositoryService(SqlAlchemyRepositoryUnitOfWork(session)).create(
            claims.principal_id,
            CreateRepository(project.id, project.name, project.slug, now),
        )
    return ProjectCreationResponse(
        id=str(project.id),
        organization_id=str(project.organization_id),
        name=project.name,
        slug=project.slug,
        default_repository=_repository_response(repository),
    )


@router.post(
    "/{project_id}/repositories",
    response_model=RepositoryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_repository(
    project_id: str,
    body: CreateRepositoryRequest,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RepositoryResponse:
    parsed_project_id = PublicId(ResourceKind.PROJECT, project_id)
    settings = get_settings()
    with create_session(settings.database_url) as session:
        repository = RepositoryService(SqlAlchemyRepositoryUnitOfWork(session)).create(
            claims.principal_id,
            CreateRepository(
                parsed_project_id,
                body.name,
                body.slug,
                datetime.now(UTC),
                body.default_branch,
            ),
        )
    return _repository_response(repository)


@router.post(
    "/{project_id}/repositories/{repository_id}/retry-provisioning",
    response_model=RepositoryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_repository_provisioning(
    project_id: str,
    repository_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RepositoryResponse:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
        repository = PublicId(ResourceKind.REPOSITORY, repository_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="repository_not_found") from error
    with create_session(get_settings().database_url) as session:
        retried = RepositoryService(
            SqlAlchemyRepositoryUnitOfWork(session)
        ).retry_provisioning(
            claims.principal_id,
            project,
            repository,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _repository_response(retried)


@router.get("/{project_id}/repositories", response_model=list[RepositoryResponse])
def list_repositories(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[RepositoryResponse]:
    try:
        parsed_project_id = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        repositories = RepositoryService(SqlAlchemyRepositoryUnitOfWork(session)).list(
            claims.principal_id, parsed_project_id
        )
    return [_repository_response(repository) for repository in repositories]


@router.delete(
    "/{project_id}/repositories/{repository_id}",
    response_model=RepositoryResponse,
)
def archive_repository(
    project_id: str,
    repository_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RepositoryResponse:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
        repository = PublicId(ResourceKind.REPOSITORY, repository_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="repository_not_found") from error
    with create_session(get_settings().database_url) as session:
        archived = RepositoryService(SqlAlchemyRepositoryUnitOfWork(session)).archive(
            claims.principal_id,
            project,
            repository,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _repository_response(archived)
