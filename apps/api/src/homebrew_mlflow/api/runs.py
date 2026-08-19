from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from homebrew_mlflow.application import (
    AccessTokenClaims,
    AuthorizationDenied,
    CreateRun,
    FinalizeRun,
    ResourceConflict,
    RunNotFound,
    RunService,
    TokenAudience,
    TrackingService,
)
from homebrew_mlflow.domain import (
    Experiment,
    MachineScope,
    PublicId,
    ResourceKind,
    Run,
    RunProvenanceStatus,
    RunState,
)
from homebrew_mlflow.infrastructure import (
    SqlAlchemyRunUnitOfWork,
    SqlAlchemyTrackingUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict, Field

from .security import access_tokens, platform_claims, run_control_claims
from .settings import get_settings
from .tracking import MetricInput, ParameterInput, TagInput

router = APIRouter(tags=["runs"])


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str
    experiment_name: str = Field(min_length=1, max_length=200)
    command: list[str] = Field(min_length=1, max_length=1000)
    retry_of_run_id: str | None = None
    pipeline_version_id: str | None = None
    environment_specification_id: str | None = None


class FinalizeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    status: Literal["succeeded", "failed", "interrupted"]
    git_commit_sha: str | None = Field(default=None, pattern="^[0-9a-f]{40,64}$")
    provenance_status: Literal["complete", "incomplete", "invalid"] | None = None
    dvc_experiment_revision: str | None = Field(default=None, pattern="^[0-9a-f]{40}$")
    evidence: dict[str, Any] = Field(default_factory=dict)
    pipeline_version_id: str | None = None
    environment_specification_id: str | None = None


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    experiment_id: str
    repository_id: str
    pipeline_version_id: str | None
    environment_specification_id: str | None
    state: str
    command: list[str]
    heartbeat_at: datetime | None
    ended_at: datetime | None
    exit_code: int | None
    provenance_status: Literal["pending", "complete", "incomplete", "invalid"]
    dvc_experiment_revision: str | None
    logging_token: str | None = None


class ExperimentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    created_at: datetime
    archived_at: datetime | None


class RunDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunResponse
    created_at: datetime
    started_at: datetime | None
    git_commit_sha: str | None
    provenance_status: Literal["pending", "complete", "incomplete", "invalid"]
    dvc_experiment_revision: str | None
    finalization_evidence: dict[str, Any] | None
    input_artifact_version_ids: list[str]
    output_artifact_version_ids: list[str]
    parameters: list[ParameterInput]
    metrics: list[MetricInput]
    tags: list[TagInput]


def _response(run: Run, *, logging_token: str | None = None) -> RunResponse:
    return RunResponse(
        id=str(run.id),
        project_id=str(run.project_id),
        experiment_id=str(run.experiment_id),
        repository_id=str(run.repository_id),
        pipeline_version_id=(
            str(run.pipeline_version_id) if run.pipeline_version_id is not None else None
        ),
        environment_specification_id=(
            str(run.environment_specification_id)
            if run.environment_specification_id is not None
            else None
        ),
        state=run.state.value,
        command=list(run.command),
        heartbeat_at=run.heartbeat_at,
        ended_at=run.ended_at,
        exit_code=run.exit_code,
        provenance_status=run.provenance_status.value,
        dvc_experiment_revision=run.dvc_experiment_revision,
        logging_token=logging_token,
    )


def _experiment_response(experiment: Experiment) -> ExperimentResponse:
    return ExperimentResponse(
        id=str(experiment.id),
        project_id=str(experiment.project_id),
        name=experiment.name,
        created_at=experiment.created_at,
        archived_at=experiment.archived_at,
    )


@router.post("/api/v1/projects/{project_id}/runs", response_model=RunResponse)
def create_run(
    project_id: str,
    body: CreateRunRequest,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RunResponse:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
        repository_id = PublicId(ResourceKind.REPOSITORY, body.repository_id)
        retry_id = (
            PublicId(ResourceKind.RUN, body.retry_of_run_id) if body.retry_of_run_id else None
        )
        pipeline_version_id = (
            PublicId(ResourceKind.PIPELINE_VERSION, body.pipeline_version_id)
            if body.pipeline_version_id
            else None
        )
        environment_specification_id = (
            PublicId(
                ResourceKind.ENVIRONMENT_SPECIFICATION,
                body.environment_specification_id,
            )
            if body.environment_specification_id
            else None
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_context_not_found") from error
    with create_session(get_settings().database_url) as session:
        try:
            run = RunService(SqlAlchemyRunUnitOfWork(session)).create(
                claims.principal_id,
                CreateRun(
                    parsed_project,
                    repository_id,
                    body.experiment_name,
                    tuple(body.command),
                    datetime.now(UTC),
                    retry_id,
                    pipeline_version_id,
                    environment_specification_id,
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="run_context_not_found") from error
    settings = get_settings()
    logging_token = access_tokens().issue(
        claims.principal_id,
        TokenAudience.MLFLOW,
        project_id=run.project_id,
        run_id=run.id,
        scopes=frozenset({MachineScope.TRACK}),
        lifetime=settings.run_logging_token_lifetime,
    )
    return _response(run, logging_token=logging_token)


@router.get("/api/v1/projects/{project_id}/runs", response_model=list[RunResponse])
def list_runs(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[RunResponse]:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        runs = RunService(SqlAlchemyRunUnitOfWork(session)).list_project(
            claims.principal_id, parsed_project
        )
    return [_response(run) for run in runs]


@router.get(
    "/api/v1/projects/{project_id}/experiments",
    response_model=list[ExperimentResponse],
)
def list_experiments(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[ExperimentResponse]:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        experiments = RunService(SqlAlchemyRunUnitOfWork(session)).list_experiments(
            claims.principal_id, parsed_project, include_archived=include_archived
        )
    return [_experiment_response(experiment) for experiment in experiments]


@router.delete(
    "/api/v1/projects/{project_id}/experiments/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_experiment(
    project_id: str,
    experiment_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
        experiment = PublicId(ResourceKind.EXPERIMENT, experiment_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="experiment_not_found") from error
    with create_session(get_settings().database_url) as session:
        RunService(SqlAlchemyRunUnitOfWork(session)).archive_experiment(
            claims.principal_id,
            project,
            experiment,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
            datetime.now(UTC),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v1/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> RunDetailResponse:
    try:
        parsed_run = PublicId(ResourceKind.RUN, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    with create_session(get_settings().database_url) as session:
        try:
            provenance = RunService(SqlAlchemyRunUnitOfWork(session)).provenance(
                claims.principal_id, parsed_run
            )
            snapshot = TrackingService(SqlAlchemyTrackingUnitOfWork(session)).snapshot_for_actor(
                claims.principal_id, parsed_run
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error
    run = provenance.run
    return RunDetailResponse(
        run=_response(run),
        created_at=run.created_at,
        started_at=run.started_at,
        git_commit_sha=run.git_commit_sha,
        provenance_status=run.provenance_status.value,
        dvc_experiment_revision=run.dvc_experiment_revision,
        finalization_evidence=run.finalization_evidence,
        input_artifact_version_ids=[str(value) for value in provenance.input_artifact_version_ids],
        output_artifact_version_ids=[
            str(value) for value in provenance.output_artifact_version_ids
        ],
        parameters=[
            ParameterInput(key=value.key, value=value.value) for value in snapshot.parameters
        ],
        metrics=[
            MetricInput(
                key=value.key,
                value=value.value,
                timestamp_ms=value.timestamp_ms,
                step=value.step,
            )
            for value in snapshot.metrics
        ],
        tags=[TagInput(key=value.key, value=value.value) for value in snapshot.tags],
    )


@router.post("/api/v1/runs/{run_id}/heartbeat", response_model=RunResponse)
def heartbeat(
    run_id: str,
    claims: Annotated[AccessTokenClaims, Depends(run_control_claims)],
) -> RunResponse:
    try:
        parsed_run = PublicId(ResourceKind.RUN, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    settings = get_settings()
    with create_session(settings.database_url) as session:
        try:
            run = RunService(SqlAlchemyRunUnitOfWork(session)).heartbeat(
                claims.principal_id, parsed_run, datetime.now(UTC)
            )
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error
    logging_token = access_tokens().issue(
        claims.principal_id,
        TokenAudience.MLFLOW,
        project_id=run.project_id,
        run_id=run.id,
        scopes=frozenset({MachineScope.TRACK}),
        lifetime=settings.run_logging_token_lifetime,
    )
    return _response(run, logging_token=logging_token)


@router.post("/api/v1/runs/{run_id}/finalize", response_model=RunResponse)
def finalize(
    run_id: str,
    body: FinalizeRunRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(run_control_claims)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ] = None,
) -> RunResponse:
    inferred_provenance = (
        RunProvenanceStatus.COMPLETE
        if body.git_commit_sha is not None and not body.evidence.get("provenance_error")
        else RunProvenanceStatus.INVALID
    )
    try:
        parsed_run = PublicId(ResourceKind.RUN, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error
    try:
        pipeline_version = (
            PublicId(ResourceKind.PIPELINE_VERSION, body.pipeline_version_id)
            if body.pipeline_version_id
            else None
        )
        environment = (
            PublicId(
                ResourceKind.ENVIRONMENT_SPECIFICATION,
                body.environment_specification_id,
            )
            if body.environment_specification_id
            else None
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid_run_finalization") from error
    with create_session(get_settings().database_url) as session:
        try:
            run = RunService(SqlAlchemyRunUnitOfWork(session)).finalize(
                claims.principal_id,
                FinalizeRun(
                    parsed_run,
                    body.exit_code,
                    RunState(body.status),
                    body.git_commit_sha,
                    body.evidence,
                    datetime.now(UTC),
                    pipeline_version,
                    environment,
                    RunProvenanceStatus(body.provenance_status)
                    if body.provenance_status is not None
                    else inferred_provenance,
                    body.dvc_experiment_revision,
                    body.provenance_status is not None,
                    idempotency_key,
                    PublicId(ResourceKind.REQUEST, request.state.request_id),
                ),
            )
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error
        except (AuthorizationDenied, ResourceConflict):
            raise
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid_run_finalization") from error
    return _response(run)
