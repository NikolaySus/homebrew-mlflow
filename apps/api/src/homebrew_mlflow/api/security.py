from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException
from homebrew_mlflow.application import (
    AccessTokenClaims,
    AccessTokenFailure,
    AccessTokenService,
    TokenAudience,
)
from homebrew_mlflow.domain import MachineScope

from .settings import get_settings


def access_tokens() -> AccessTokenService:
    settings = get_settings()
    return AccessTokenService(
        settings.access_token_signing_key.get_secret_value(),
        str(settings.public_base_url).rstrip("/"),
        key_id=settings.access_token_key_id,
    )


def platform_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication_required")
    try:
        return access_tokens().verify(
            authorization.removeprefix("Bearer "), TokenAudience.PLATFORM_API
        )
    except AccessTokenFailure as error:
        raise HTTPException(status_code=401, detail="invalid_access_token") from error


def mlflow_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication_required")
    try:
        claims = access_tokens().verify(authorization.removeprefix("Bearer "), TokenAudience.MLFLOW)
    except AccessTokenFailure as error:
        raise HTTPException(status_code=401, detail="invalid_access_token") from error
    if claims.project_id is None or claims.run_id is None:
        raise HTTPException(status_code=401, detail="run_binding_required")
    return claims


def dvc_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication_required")
    try:
        claims = access_tokens().verify(
            authorization.removeprefix("Bearer "), TokenAudience.DVC_CREDENTIALS
        )
    except AccessTokenFailure as error:
        raise HTTPException(status_code=401, detail="invalid_access_token") from error
    if claims.project_id is None or MachineScope.DVC_TRANSFER not in claims.scopes:
        raise HTTPException(status_code=401, detail="project_binding_required")
    return claims


def publication_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication_required")
    try:
        claims = access_tokens().verify(
            authorization.removeprefix("Bearer "), TokenAudience.PUBLICATION
        )
    except AccessTokenFailure as error:
        raise HTTPException(status_code=401, detail="invalid_access_token") from error
    if claims.project_id is None or MachineScope.PUBLISH not in claims.scopes:
        raise HTTPException(status_code=401, detail="project_binding_required")
    return claims
