from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from homebrew_mlflow.application import (
    AccessTokenClaims,
    ProjectMembershipService,
    ProjectMembershipView,
)
from homebrew_mlflow.domain import ProjectRole, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyMembershipUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/projects", tags=["project-memberships"])


class SetProjectMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ProjectRole


class ProjectMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    display_name: str
    principal_kind: str
    gitlab_username: str | None
    role: ProjectRole
    created_at: datetime


def _id(kind: ResourceKind, value: str, detail: str) -> PublicId:
    try:
        return PublicId(kind, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=detail) from error


def _response(value: ProjectMembershipView) -> ProjectMembershipResponse:
    return ProjectMembershipResponse(
        principal_id=str(value.principal.id),
        display_name=value.principal.display_name,
        principal_kind=value.principal.kind.value,
        gitlab_username=value.gitlab_username,
        role=value.membership.role,
        created_at=value.membership.created_at,
    )


@router.get("/{project_id}/memberships", response_model=list[ProjectMembershipResponse])
def list_memberships(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[ProjectMembershipResponse]:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    with create_session(get_settings().database_url) as session:
        values = ProjectMembershipService(SqlAlchemyMembershipUnitOfWork(session)).list(
            claims.principal_id, project
        )
    return [_response(value) for value in values]


@router.put(
    "/{project_id}/memberships/{principal_id}",
    response_model=ProjectMembershipResponse,
)
def set_membership(
    project_id: str,
    principal_id: str,
    body: SetProjectMembershipRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ProjectMembershipResponse:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    principal = _id(ResourceKind.PRINCIPAL, principal_id, "principal_not_found")
    request_id = PublicId(ResourceKind.REQUEST, request.state.request_id)
    with create_session(get_settings().database_url) as session:
        value = ProjectMembershipService(SqlAlchemyMembershipUnitOfWork(session)).set(
            claims.principal_id,
            project,
            principal,
            body.role,
            request_id,
            datetime.now(UTC),
        )
    return _response(value)


@router.delete(
    "/{project_id}/memberships/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_membership(
    project_id: str,
    principal_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    principal = _id(ResourceKind.PRINCIPAL, principal_id, "principal_not_found")
    request_id = PublicId(ResourceKind.REQUEST, request.state.request_id)
    with create_session(get_settings().database_url) as session:
        ProjectMembershipService(SqlAlchemyMembershipUnitOfWork(session)).remove(
            claims.principal_id,
            project,
            principal,
            request_id,
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/memberships/{principal_id}/recover-maintainer",
    response_model=ProjectMembershipResponse,
)
def recover_maintainer(
    project_id: str,
    principal_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> ProjectMembershipResponse:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    principal = _id(ResourceKind.PRINCIPAL, principal_id, "principal_not_found")
    with create_session(get_settings().database_url) as session:
        value = ProjectMembershipService(
            SqlAlchemyMembershipUnitOfWork(session)
        ).recover_maintainer(
            claims.principal_id,
            project,
            principal,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _response(value)
