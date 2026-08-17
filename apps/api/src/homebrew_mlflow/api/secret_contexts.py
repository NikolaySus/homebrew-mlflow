from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from homebrew_mlflow.application import AccessTokenClaims, SecretContextService
from homebrew_mlflow.domain import PublicId, ResourceKind, SecretContext
from homebrew_mlflow.infrastructure import SqlAlchemySecretContextUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(tags=["infisical"])


class ConfigureSecretContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infisical_project_id: str = Field(min_length=1, max_length=200)
    environment_slug: str = Field(min_length=1, max_length=100)
    secret_path: str = Field(default="/", min_length=1, max_length=1000)


class SecretContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    infisical_project_id: str
    environment_slug: str
    secret_path: str
    reconciliation_state: str
    last_error_code: str | None
    updated_at: datetime


def _project(value: str) -> PublicId:
    try:
        return PublicId(ResourceKind.PROJECT, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error


def _response(context: SecretContext) -> SecretContextResponse:
    return SecretContextResponse(
        project_id=str(context.project_id),
        infisical_project_id=context.infisical_project_id,
        environment_slug=context.environment_slug,
        secret_path=context.secret_path,
        reconciliation_state=context.reconciliation_state,
        last_error_code=context.last_error_code,
        updated_at=context.updated_at,
    )


@router.get("/api/v1/projects/{project_id}/secret-context", response_model=SecretContextResponse)
def get_secret_context(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> SecretContextResponse:
    with create_session(get_settings().database_url) as session:
        context = SecretContextService(SqlAlchemySecretContextUnitOfWork(session)).get(
            claims.principal_id, _project(project_id)
        )
    if context is None:
        raise HTTPException(status_code=404, detail="secret_context_not_configured")
    return _response(context)


@router.put("/api/v1/projects/{project_id}/secret-context", response_model=SecretContextResponse)
def configure_secret_context(
    project_id: str,
    body: ConfigureSecretContextRequest,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> SecretContextResponse:
    with create_session(get_settings().database_url) as session:
        context = SecretContextService(SqlAlchemySecretContextUnitOfWork(session)).configure(
            claims.principal_id,
            _project(project_id),
            body.infisical_project_id,
            body.environment_slug,
            body.secret_path,
            datetime.now(UTC),
        )
    return _response(context)
