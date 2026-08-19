from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from homebrew_mlflow.application import AccessTokenClaims
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


class MlflowDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    backend_status: int


@router.get("/mlflow", response_model=MlflowDiagnosticResponse)
def mlflow_diagnostic(
    _claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MlflowDiagnosticResponse:
    try:
        response = httpx.get(
            f"{str(get_settings().mlflow_internal_url).rstrip('/')}/health", timeout=3
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="mlflow_unavailable") from error
    if response.status_code not in {200, 401}:
        raise HTTPException(status_code=503, detail="mlflow_unavailable")
    return MlflowDiagnosticResponse(status="ready", backend_status=response.status_code)
