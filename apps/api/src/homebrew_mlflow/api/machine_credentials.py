from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from homebrew_mlflow.application import (
    AccessTokenClaims,
    AuthorizationDenied,
    MachineCredentialService,
    TokenAudience,
)
from homebrew_mlflow.domain import MachineScope, ProjectRole, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyMachineCredentialStore, create_session
from pydantic import BaseModel, ConfigDict, Field

from .auth import AccessTokenResponse
from .security import access_tokens, platform_claims
from .settings import get_settings

router = APIRouter(tags=["machine-credentials"])


class CreateMachineCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["viewer", "contributor"]
    scopes: list[MachineScope] = Field(min_length=1)


class MachineCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    principal_id: str
    project_id: str
    secret: str
    scopes: list[str]
    expires_at: datetime


class MachineCredentialSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    principal_id: str
    project_id: str
    scopes: list[str]
    revoked: bool
    expires_at: datetime


class MachineLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    secret: str


@router.post(
    "/api/v1/projects/{project_id}/machine-credentials",
    response_model=MachineCredentialResponse,
    status_code=201,
)
def create_machine_credential(
    project_id: str,
    body: CreateMachineCredentialRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MachineCredentialResponse:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        created = MachineCredentialService(SqlAlchemyMachineCredentialStore(session)).create(
            claims.principal_id,
            project,
            body.display_name,
            ProjectRole(body.role),
            frozenset(body.scopes),
            datetime.now(UTC),
            PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return MachineCredentialResponse(
        credential_id=str(created.id),
        principal_id=str(created.principal_id),
        project_id=str(created.project_id),
        secret=created.secret,
        scopes=sorted(scope.value for scope in created.scopes),
        expires_at=created.expires_at,
    )


@router.get(
    "/api/v1/projects/{project_id}/machine-credentials",
    response_model=list[MachineCredentialSummary],
)
def list_machine_credentials(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[MachineCredentialSummary]:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        records = MachineCredentialService(SqlAlchemyMachineCredentialStore(session)).list(
            claims.principal_id, project
        )
    return [
        MachineCredentialSummary(
            credential_id=str(record.id),
            principal_id=str(record.principal_id),
            project_id=str(record.project_id),
            scopes=sorted(scope.value for scope in record.scopes),
            revoked=record.revoked,
            expires_at=record.expires_at,
        )
        for record in records
    ]


@router.delete(
    "/api/v1/machine-credentials/{credential_id}",
    response_model=MachineCredentialSummary,
)
def revoke_machine_credential(
    credential_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MachineCredentialSummary:
    try:
        parsed = PublicId(ResourceKind.MACHINE_CREDENTIAL, credential_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="machine_credential_not_found") from error
    with create_session(get_settings().database_url) as session:
        try:
            record = MachineCredentialService(SqlAlchemyMachineCredentialStore(session)).revoke(
                claims.principal_id,
                parsed,
                datetime.now(UTC),
                PublicId(ResourceKind.REQUEST, request.state.request_id),
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="machine_credential_not_found") from error
    return MachineCredentialSummary(
        credential_id=str(record.id),
        principal_id=str(record.principal_id),
        project_id=str(record.project_id),
        scopes=sorted(scope.value for scope in record.scopes),
        revoked=record.revoked,
        expires_at=record.expires_at,
    )


@router.post("/api/v1/auth/machine", response_model=AccessTokenResponse)
def machine_login(body: MachineLoginRequest, request: Request) -> AccessTokenResponse:
    try:
        credential_id = PublicId(ResourceKind.MACHINE_CREDENTIAL, body.credential_id)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="invalid_machine_credential") from error
    with create_session(get_settings().database_url) as session:
        try:
            record = MachineCredentialService(
                SqlAlchemyMachineCredentialStore(session)
            ).authenticate(
                credential_id,
                body.secret,
                datetime.now(UTC),
                PublicId(ResourceKind.REQUEST, request.state.request_id),
            )
        except AuthorizationDenied as error:
            raise HTTPException(status_code=401, detail="invalid_machine_credential") from error
    token = access_tokens().issue(
        record.principal_id,
        TokenAudience.PLATFORM_API,
        project_id=record.project_id,
        scopes=record.scopes,
    )
    return AccessTokenResponse(access_token=token)
