from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from homebrew_mlflow.application import AccessTokenClaims, DvcCredentialService
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    MinioDvcCredentialIssuer,
    SqlAlchemyProjectUnitOfWork,
    SqlAlchemyRepositoryUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict, Field

from .security import dvc_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/projects", tags=["dvc-credentials"])


class AwsCredentialProcessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal[1] = Field(default=1, alias="Version")
    access_key_id: str = Field(alias="AccessKeyId")
    secret_access_key: str = Field(alias="SecretAccessKey")
    session_token: str = Field(alias="SessionToken")
    expiration: datetime = Field(alias="Expiration")


@lru_cache
def credential_issuer(
    endpoint_url: str, bucket: str, access_key_id: str, secret_access_key: str
) -> MinioDvcCredentialIssuer:
    return MinioDvcCredentialIssuer(endpoint_url, bucket, access_key_id, secret_access_key)


@router.post("/{project_id}/dvc-credentials", response_model=AwsCredentialProcessResponse)
def issue_dvc_credentials(
    project_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(dvc_claims)],
    recovery_run_id: Annotated[str | None, Query()] = None,
) -> AwsCredentialProcessResponse:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    if claims.project_id != parsed_project:
        raise HTTPException(status_code=403, detail="project_scope_mismatch")
    try:
        parsed_recovery_run = (
            PublicId(ResourceKind.RUN, recovery_run_id) if recovery_run_id is not None else None
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    settings = get_settings()
    issuer = credential_issuer(
        settings.s3_endpoint_url,
        settings.dvc_bucket,
        settings.s3_access_key_id,
        settings.s3_secret_access_key.get_secret_value(),
    )
    with create_session(settings.database_url) as session:
        credential = DvcCredentialService(
            SqlAlchemyRepositoryUnitOfWork(session),
            issuer,
            SqlAlchemyProjectUnitOfWork(session),
        ).issue(
            claims.principal_id,
            parsed_project,
            parsed_recovery_run,
            request_id=PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return AwsCredentialProcessResponse(
        AccessKeyId=credential.access_key_id,
        SecretAccessKey=credential.secret_access_key,
        SessionToken=credential.session_token,
        Expiration=credential.expiration,
    )
