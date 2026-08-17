from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from homebrew_mlflow.application import (
    AccessTokenClaims,
    LogBatch,
    MetricValue,
    ParameterValue,
    TagValue,
    TrackingService,
)
from homebrew_mlflow.domain import MachineScope, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyTrackingUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import mlflow_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/runs", tags=["tracking"])


class ParameterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=250)
    value: str = Field(max_length=6000)


class MetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=250)
    value: float
    timestamp_ms: int = Field(ge=0)
    step: int = Field(default=0, ge=0)


class TagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=250)
    value: str = Field(max_length=8000)


class LogBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: list[ParameterInput] = Field(default_factory=list, max_length=1000)
    metrics: list[MetricInput] = Field(default_factory=list, max_length=1000)
    tags: list[TagInput] = Field(default_factory=list, max_length=1000)


class TrackingRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    experiment_id: str
    project_id: str
    state: str
    started_at: datetime | None
    ended_at: datetime | None
    attachment_uri: str
    parameters: list[ParameterInput]
    metrics: list[MetricInput]
    tags: list[TagInput]


def _authorize_binding(claims: AccessTokenClaims, run_id: PublicId) -> PublicId:
    if (
        claims.project_id is None
        or claims.run_id != run_id
        or MachineScope.TRACK not in claims.scopes
    ):
        raise HTTPException(status_code=403, detail="run_scope_mismatch")
    return claims.project_id


@router.get("/{run_id}/tracking", response_model=TrackingRunResponse)
def get_tracking_run(
    run_id: str,
    claims: Annotated[AccessTokenClaims, Depends(mlflow_claims)],
) -> TrackingRunResponse:
    try:
        parsed_run = PublicId(ResourceKind.RUN, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    project_id = _authorize_binding(claims, parsed_run)
    with create_session(get_settings().database_url) as session:
        try:
            snapshot = TrackingService(SqlAlchemyTrackingUnitOfWork(session)).snapshot(
                claims.principal_id, parsed_run, project_id
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error
    return TrackingRunResponse(
        id=str(snapshot.run.id),
        experiment_id=str(snapshot.run.experiment_id),
        project_id=str(snapshot.run.project_id),
        state=snapshot.run.state.value,
        started_at=snapshot.run.started_at,
        ended_at=snapshot.run.ended_at,
        attachment_uri=f"homebrew://{snapshot.run.id}",
        parameters=[ParameterInput(key=item.key, value=item.value) for item in snapshot.parameters],
        metrics=[
            MetricInput(
                key=item.key,
                value=item.value,
                timestamp_ms=item.timestamp_ms,
                step=item.step,
            )
            for item in snapshot.metrics
        ],
        tags=[TagInput(key=item.key, value=item.value) for item in snapshot.tags],
    )


@router.post("/{run_id}/tracking/batch", status_code=204)
def log_batch(
    run_id: str,
    body: LogBatchRequest,
    claims: Annotated[AccessTokenClaims, Depends(mlflow_claims)],
) -> Response:
    try:
        parsed_run = PublicId(ResourceKind.RUN, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    project_id = _authorize_binding(claims, parsed_run)
    now = datetime.now(UTC)
    with create_session(get_settings().database_url) as session:
        try:
            TrackingService(SqlAlchemyTrackingUnitOfWork(session)).log_batch(
                claims.principal_id,
                LogBatch(
                    parsed_run,
                    project_id,
                    tuple(ParameterValue(item.key, item.value) for item in body.parameters),
                    tuple(
                        MetricValue(item.key, item.value, item.timestamp_ms, item.step)
                        for item in body.metrics
                    ),
                    tuple(TagValue(item.key, item.value) for item in body.tags),
                    now,
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid_tracking_batch") from error
    return Response(status_code=204)
