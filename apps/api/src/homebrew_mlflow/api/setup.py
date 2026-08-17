from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from homebrew_mlflow.application import (
    AccessTokenClaims,
    ClaimInstallation,
    SetupService,
)
from homebrew_mlflow.infrastructure import SqlAlchemySetupStore, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])


class InstallationClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(min_length=1, max_length=200)
    bootstrap_token: str = Field(min_length=1)


class InstallationClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    principal_id: str
    role: str


@router.post("/claim", response_model=InstallationClaimResponse)
def claim_installation(
    request: InstallationClaimRequest,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> InstallationClaimResponse:
    settings = get_settings()
    expected = hashlib.sha256(
        settings.bootstrap_token.get_secret_value().encode("utf-8")
    ).hexdigest()
    with create_session(settings.database_url) as session:
        organization = SetupService(SqlAlchemySetupStore(session), expected).claim(
            ClaimInstallation(
                claims.principal_id,
                request.organization_name,
                request.bootstrap_token,
                datetime.now(UTC),
            )
        )
    return InstallationClaimResponse(
        organization_id=str(organization.id),
        principal_id=str(claims.principal_id),
        role="admin",
    )
