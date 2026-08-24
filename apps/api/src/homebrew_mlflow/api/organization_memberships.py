from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from homebrew_mlflow.application import (
    AccessTokenClaims,
    OrganizationMembershipService,
    OrganizationPrincipalView,
)
from homebrew_mlflow.domain import OrganizationRole, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    SqlAlchemyOrganizationMembershipUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/organizations", tags=["organization-memberships"])


class SetOrganizationMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: OrganizationRole


class OrganizationPrincipalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    display_name: str
    principal_kind: str
    gitlab_username: str | None
    organization_role: OrganizationRole | None
    created_at: datetime
    membership_created_at: datetime | None


def _id(kind: ResourceKind, value: str, detail: str) -> PublicId:
    try:
        return PublicId(kind, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=detail) from error


def _response(value: OrganizationPrincipalView) -> OrganizationPrincipalResponse:
    return OrganizationPrincipalResponse(
        principal_id=str(value.principal.id),
        display_name=value.principal.display_name,
        principal_kind=value.principal.kind.value,
        gitlab_username=value.gitlab_username,
        organization_role=value.role,
        created_at=value.principal.created_at,
        membership_created_at=value.membership_created_at,
    )


@router.get("/{organization_id}/principals", response_model=list[OrganizationPrincipalResponse])
def list_organization_principals(
    organization_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[OrganizationPrincipalResponse]:
    organization = _id(ResourceKind.ORGANIZATION, organization_id, "organization_not_found")
    with create_session(get_settings().database_url) as session:
        values = OrganizationMembershipService(
            SqlAlchemyOrganizationMembershipUnitOfWork(session)
        ).list(claims.principal_id, organization)
    return [_response(value) for value in values]


@router.put(
    "/{organization_id}/memberships/{principal_id}",
    response_model=OrganizationPrincipalResponse,
)
def set_organization_membership(
    organization_id: str,
    principal_id: str,
    body: SetOrganizationMembershipRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> OrganizationPrincipalResponse:
    organization = _id(ResourceKind.ORGANIZATION, organization_id, "organization_not_found")
    principal = _id(ResourceKind.PRINCIPAL, principal_id, "principal_not_found")
    with create_session(get_settings().database_url) as session:
        value = OrganizationMembershipService(
            SqlAlchemyOrganizationMembershipUnitOfWork(session)
        ).set(
            claims.principal_id,
            organization,
            principal,
            body.role,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _response(value)


@router.delete(
    "/{organization_id}/memberships/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_organization_membership(
    organization_id: str,
    principal_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    organization = _id(ResourceKind.ORGANIZATION, organization_id, "organization_not_found")
    principal = _id(ResourceKind.PRINCIPAL, principal_id, "principal_not_found")
    with create_session(get_settings().database_url) as session:
        OrganizationMembershipService(
            SqlAlchemyOrganizationMembershipUnitOfWork(session)
        ).remove(
            claims.principal_id,
            organization,
            principal,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
