from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from homebrew_mlflow.application import (
    AccessTokenClaims,
    ArtifactCatalogService,
    ProjectService,
    RunService,
    TrackingService,
)
from homebrew_mlflow.domain import (
    MachineScope,
    ProjectState,
    PublicId,
    ResearchProject,
    ResourceKind,
)
from homebrew_mlflow.infrastructure import (
    SqlAlchemyArtifactCatalogUnitOfWork,
    SqlAlchemyProjectUnitOfWork,
    SqlAlchemyRunUnitOfWork,
    SqlAlchemyTrackingUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict

from .security import mlflow_read_claims, mlflow_workspace_claims
from .settings import get_settings
from .tracking import MetricInput, ParameterInput, TagInput

router = APIRouter(prefix="/api/v1/mlflow", tags=["mlflow-compatibility"])


def workspace_name(project_id: PublicId) -> str:
    return str(project_id).replace("pr_", "pr-", 1).lower()


def workspace_project(value: str) -> PublicId:
    normalized = value.strip().lower()
    if not normalized.startswith("pr-"):
        raise HTTPException(status_code=404, detail="workspace_not_found")
    try:
        return PublicId(ResourceKind.PROJECT, "pr_" + normalized.removeprefix("pr-").upper())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="workspace_not_found") from error


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    project_id: str
    project_name: str
    project_slug: str


class MlflowExperimentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    created_at: datetime
    archived_at: datetime | None
    last_update_at: datetime


class MlflowRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    experiment_id: str
    creator_principal_id: str
    state: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    attachment_uri: str
    parameters: list[ParameterInput]
    metrics: list[MetricInput]
    tags: list[TagInput]
    input_artifact_version_ids: list[str]
    output_artifact_version_ids: list[str]


class MlflowArtifactAliasResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    artifact_version_id: str


class MlflowArtifactVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    artifact_id: str
    sequence: int
    mlflow_model_id: str
    algorithm: str
    digest: str
    output_kind: str
    size: int
    file_count: int
    published_at: datetime
    producing_run_id: str | None


class MlflowArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: str
    description: str | None
    created_at: datetime
    versions: list[MlflowArtifactVersionResponse]
    aliases: list[MlflowArtifactAliasResponse]


class MlflowCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[MlflowArtifactResponse]


class MlflowProjectSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceResponse
    experiments: list[MlflowExperimentResponse]
    runs: list[MlflowRunResponse]


def _workspace(value: ResearchProject) -> WorkspaceResponse:
    return WorkspaceResponse(
        name=workspace_name(value.id),
        project_id=str(value.id),
        project_name=value.name,
        project_slug=value.slug,
    )


@router.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(
    claims: Annotated[AccessTokenClaims, Depends(mlflow_workspace_claims)],
) -> list[WorkspaceResponse]:
    with create_session(get_settings().database_url) as session:
        projects = ProjectService(SqlAlchemyProjectUnitOfWork(session)).list_for_actor(
            claims.principal_id
        )
    return [
        _workspace(project)
        for project in projects
        if project.state is ProjectState.ACTIVE
        and (MachineScope.READ in claims.scopes or project.id == claims.project_id)
    ]


@router.get(
    "/workspaces/{workspace}/snapshot",
    response_model=MlflowProjectSnapshotResponse,
)
def project_snapshot(
    workspace: str,
    claims: Annotated[AccessTokenClaims, Depends(mlflow_read_claims)],
) -> MlflowProjectSnapshotResponse:
    project_id = workspace_project(workspace)
    if claims.project_id != project_id:
        raise HTTPException(status_code=403, detail="workspace_scope_mismatch")
    settings = get_settings()
    with create_session(settings.database_url) as session:
        project_service = ProjectService(SqlAlchemyProjectUnitOfWork(session))
        project = next(
            (
                value
                for value in project_service.list_for_actor(claims.principal_id)
                if value.id == project_id and value.state is ProjectState.ACTIVE
            ),
            None,
        )
        if project is None:
            raise HTTPException(status_code=404, detail="workspace_not_found")
        run_service = RunService(SqlAlchemyRunUnitOfWork(session))
        runs = run_service.list_project(claims.principal_id, project_id)
        experiments = run_service.list_experiments(
            claims.principal_id, project_id, include_archived=True
        )
        tracking = TrackingService(SqlAlchemyTrackingUnitOfWork(session))
        snapshots = tracking.project_snapshots(claims.principal_id, project_id)
        provenance = {
            run.id: run_service.provenance(claims.principal_id, run.id) for run in runs
        }
    last_updates = {experiment.id: experiment.created_at for experiment in experiments}
    for run in runs:
        candidate = run.ended_at or run.heartbeat_at or run.started_at or run.created_at
        last_updates[run.experiment_id] = max(last_updates[run.experiment_id], candidate)
    return MlflowProjectSnapshotResponse(
        workspace=_workspace(project),
        experiments=[
            MlflowExperimentResponse(
                id=str(experiment.id),
                name=experiment.name,
                created_at=experiment.created_at,
                archived_at=experiment.archived_at,
                last_update_at=last_updates[experiment.id],
            )
            for experiment in experiments
        ],
        runs=[
            MlflowRunResponse(
                id=str(snapshot.run.id),
                experiment_id=str(snapshot.run.experiment_id),
                creator_principal_id=str(snapshot.run.creator_principal_id),
                state=snapshot.run.state.value,
                created_at=snapshot.run.created_at,
                started_at=snapshot.run.started_at,
                ended_at=snapshot.run.ended_at,
                attachment_uri=f"homebrew://{snapshot.run.id}",
                parameters=[
                    ParameterInput(key=item.key, value=item.value)
                    for item in snapshot.parameters
                ],
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
                input_artifact_version_ids=[
                    str(value)
                    for value in provenance[snapshot.run.id].input_artifact_version_ids
                ],
                output_artifact_version_ids=[
                    str(value)
                    for value in provenance[snapshot.run.id].output_artifact_version_ids
                ],
            )
            for snapshot in snapshots
        ],
    )


@router.get(
    "/workspaces/{workspace}/catalog",
    response_model=MlflowCatalogResponse,
)
def project_catalog(
    workspace: str,
    claims: Annotated[AccessTokenClaims, Depends(mlflow_read_claims)],
) -> MlflowCatalogResponse:
    project_id = workspace_project(workspace)
    if claims.project_id != project_id:
        raise HTTPException(status_code=403, detail="workspace_scope_mismatch")
    with create_session(get_settings().database_url) as session:
        service = ArtifactCatalogService(SqlAlchemyArtifactCatalogUnitOfWork(session))
        artifacts = service.list_artifacts(claims.principal_id, project_id)
        response: list[MlflowArtifactResponse] = []
        for artifact in artifacts:
            versions = service.list_versions(claims.principal_id, artifact.id)
            aliases = service.list_aliases(claims.principal_id, artifact.id)
            response.append(
                MlflowArtifactResponse(
                    id=str(artifact.id),
                    name=artifact.name,
                    kind=artifact.kind.value,
                    description=artifact.description,
                    created_at=artifact.created_at,
                    versions=[
                        MlflowArtifactVersionResponse(
                            id=str(version.id),
                            artifact_id=str(version.artifact_id),
                            sequence=version.sequence,
                            mlflow_model_id=version.mlflow_model_id,
                            algorithm=version.identity.algorithm,
                            digest=version.identity.digest,
                            output_kind=version.identity.kind.value,
                            size=version.identity.size,
                            file_count=version.identity.file_count,
                            published_at=version.published_at,
                            producing_run_id=(
                                str(version.producing_run_id)
                                if version.producing_run_id is not None
                                else None
                            ),
                        )
                        for version in versions
                    ],
                    aliases=[
                        MlflowArtifactAliasResponse(
                            alias=value.alias,
                            artifact_version_id=str(value.artifact_version_id),
                        )
                        for value in aliases
                    ],
                )
            )
    return MlflowCatalogResponse(artifacts=response)
