from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from homebrew_mlflow.application import (
    AccessTokenClaims,
    MetricProgressCatalog,
    MetricProgressService,
)
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyMetricProgressUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/projects/{project_id}/metric-progress", tags=["runs"])


class MetricProgressMetricResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    experiment_count: int
    latest_run_at: datetime


class MetricProgressCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_metric_key: str | None
    metrics: list[MetricProgressMetricResponse]


class MetricProgressPointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    experiment_name: str
    run_id: str
    run_state: str
    value: float
    run_at: datetime
    metric_timestamp_ms: int
    metric_step: int


class MetricProgressPointsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_key: str
    points: list[MetricProgressPointResponse]


class SetDefaultMetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_key: str | None = Field(default=None, max_length=250)


def _project_id(value: str) -> PublicId:
    try:
        return PublicId(ResourceKind.PROJECT, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error


def _catalog_response(catalog: MetricProgressCatalog) -> MetricProgressCatalogResponse:
    return MetricProgressCatalogResponse(
        default_metric_key=catalog.default_metric_key,
        metrics=[
            MetricProgressMetricResponse(
                key=value.key,
                experiment_count=value.experiment_count,
                latest_run_at=value.latest_run_at,
            )
            for value in catalog.metrics
        ],
    )


@router.get("", response_model=MetricProgressCatalogResponse)
def metric_progress_catalog(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MetricProgressCatalogResponse:
    with create_session(get_settings().database_url) as session:
        catalog = MetricProgressService(SqlAlchemyMetricProgressUnitOfWork(session)).catalog(
            claims.principal_id, _project_id(project_id)
        )
    return _catalog_response(catalog)


@router.get("/points", response_model=MetricProgressPointsResponse)
def metric_progress_points(
    project_id: str,
    metric_key: Annotated[str, Query(min_length=1, max_length=250)],
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MetricProgressPointsResponse:
    parsed = _project_id(project_id)
    with create_session(get_settings().database_url) as session:
        points = MetricProgressService(SqlAlchemyMetricProgressUnitOfWork(session)).points(
            claims.principal_id, parsed, metric_key
        )
    return MetricProgressPointsResponse(
        metric_key=metric_key.strip(),
        points=[
            MetricProgressPointResponse(
                experiment_id=str(value.experiment_id),
                experiment_name=value.experiment_name,
                run_id=str(value.run_id),
                run_state=value.run_state,
                value=value.value,
                run_at=value.run_at,
                metric_timestamp_ms=value.metric_timestamp_ms,
                metric_step=value.metric_step,
            )
            for value in points
        ],
    )


@router.put("/default", response_model=MetricProgressCatalogResponse)
def set_metric_progress_default(
    project_id: str,
    body: SetDefaultMetricRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> MetricProgressCatalogResponse:
    parsed = _project_id(project_id)
    with create_session(get_settings().database_url) as session:
        catalog = MetricProgressService(SqlAlchemyMetricProgressUnitOfWork(session)).set_default(
            claims.principal_id,
            parsed,
            body.metric_key,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return _catalog_response(catalog)
