from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from homebrew_mlflow.application import AccessTokenClaims, IdentityViewService
from homebrew_mlflow.infrastructure import SqlAlchemyIdentityReadStore, create_session
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["identity"])


class RoleBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    role: str


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    kind: str
    display_name: str
    created_at: datetime
    organizations: list[RoleBindingResponse]
    projects: list[RoleBindingResponse]


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    created_at: datetime


@router.get("/me", response_model=MeResponse)
def get_me(
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MeResponse:
    with create_session(get_settings().database_url) as session:
        try:
            value = IdentityViewService(SqlAlchemyIdentityReadStore(session)).me(
                claims.principal_id
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="principal_not_found") from error
    return MeResponse(
        principal_id=str(value.principal.id),
        kind=value.principal.kind.value,
        display_name=value.principal.display_name,
        created_at=value.principal.created_at,
        organizations=[
            RoleBindingResponse(resource_id=str(item.organization_id), role=item.role.value)
            for item in value.organizations
        ],
        projects=[
            RoleBindingResponse(resource_id=str(item.project_id), role=item.role.value)
            for item in value.projects
        ],
    )


@router.get("/organization", response_model=OrganizationResponse)
def get_organization(
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> OrganizationResponse:
    with create_session(get_settings().database_url) as session:
        try:
            value = IdentityViewService(SqlAlchemyIdentityReadStore(session)).organization(
                claims.principal_id
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="organization_not_found") from error
    return OrganizationResponse(id=str(value.id), name=value.name, created_at=value.created_at)
