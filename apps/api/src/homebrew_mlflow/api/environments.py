import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from homebrew_mlflow.application import AccessTokenClaims, EnvironmentService
from homebrew_mlflow.domain import (
    EnvironmentKind,
    EnvironmentSpecification,
    PublicId,
    ResourceKind,
)
from homebrew_mlflow.infrastructure import SqlAlchemyEnvironmentUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["environments"])


class CreateEnvironmentSpecificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: EnvironmentKind
    document: dict[str, Any]


class EnvironmentSpecificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    kind: EnvironmentKind
    document: dict[str, Any]
    sha256: str
    created_at: datetime
    archived_at: datetime | None


def _id(value: str) -> PublicId:
    try:
        return PublicId(ResourceKind.ENVIRONMENT_SPECIFICATION, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="environment_not_found") from error


def _response(value: EnvironmentSpecification) -> EnvironmentSpecificationResponse:
    return EnvironmentSpecificationResponse(
        id=str(value.id),
        project_id=str(value.project_id),
        name=value.name,
        kind=value.kind,
        document=json.loads(value.canonical_document),
        sha256=value.sha256,
        created_at=value.created_at,
        archived_at=value.archived_at,
    )


@router.get(
    "/projects/{project_id}/environment-specifications",
    response_model=list[EnvironmentSpecificationResponse],
)
def list_environment_specifications(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[EnvironmentSpecificationResponse]:
    try:
        parsed = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        values = EnvironmentService(SqlAlchemyEnvironmentUnitOfWork(session)).list(
            claims.principal_id, parsed, include_archived=include_archived
        )
    return [_response(value) for value in values]


@router.post(
    "/projects/{project_id}/environment-specifications",
    response_model=EnvironmentSpecificationResponse,
)
def create_environment_specification(
    project_id: str,
    body: CreateEnvironmentSpecificationRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> EnvironmentSpecificationResponse:
    try:
        parsed = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        value = EnvironmentService(SqlAlchemyEnvironmentUnitOfWork(session)).create(
            claims.principal_id,
            parsed,
            body.name,
            body.kind,
            body.document,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _response(value)


@router.put(
    "/projects/{project_id}/environment-specifications/resolve",
    response_model=EnvironmentSpecificationResponse,
)
def resolve_environment_specification(
    project_id: str,
    body: CreateEnvironmentSpecificationRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> EnvironmentSpecificationResponse:
    return create_environment_specification(project_id, body, request, claims)


@router.delete(
    "/environment-specifications/{specification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_environment_specification(
    specification_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    with create_session(get_settings().database_url) as session:
        EnvironmentService(SqlAlchemyEnvironmentUnitOfWork(session)).archive(
            claims.principal_id,
            _id(specification_id),
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
