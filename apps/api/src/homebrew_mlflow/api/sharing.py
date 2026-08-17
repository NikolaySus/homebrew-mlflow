from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from homebrew_mlflow.application import AccessTokenClaims, ArtifactSharingService
from homebrew_mlflow.domain import ArtifactSharingGrant, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemySharingUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(tags=["artifact-sharing"])


class CreateSharingGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consuming_project_id: str


class CreateSharedReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version_id: str
    run_id: str | None = None


class SharingGrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    artifact_version_id: str
    owning_project_id: str
    consuming_project_id: str
    created_at: datetime
    effective_at: datetime
    revoked_at: datetime | None


class SharedReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    artifact_version_id: str
    grant_id: str
    consuming_project_id: str
    run_id: str | None
    created_at: datetime


class CreateDerivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_version_id: str


class DerivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_artifact_version_id: str
    derived_artifact_version_id: str
    created_at: datetime


def _id(kind: ResourceKind, value: str, not_found: str) -> PublicId:
    try:
        return PublicId(kind, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=not_found) from error


def _grant_response(grant: ArtifactSharingGrant) -> SharingGrantResponse:
    return SharingGrantResponse(
        id=str(grant.id),
        artifact_version_id=str(grant.version_id),
        owning_project_id=str(grant.owning_project_id),
        consuming_project_id=str(grant.consuming_project_id),
        created_at=grant.created_at,
        effective_at=grant.effective_at,
        revoked_at=grant.revoked_at,
    )


@router.post(
    "/api/v1/artifact-versions/{version_id}/sharing-grants",
    response_model=SharingGrantResponse,
    status_code=201,
)
def create_grant(
    version_id: str,
    body: CreateSharingGrantRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> SharingGrantResponse:
    version = _id(ResourceKind.ARTIFACT_VERSION, version_id, "artifact_version_not_found")
    consumer = _id(ResourceKind.PROJECT, body.consuming_project_id, "project_not_found")
    with create_session(get_settings().database_url) as session:
        grant = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session)).grant(
            claims.principal_id,
            version,
            consumer,
            datetime.now(UTC),
            PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return _grant_response(grant)


@router.get(
    "/api/v1/artifact-versions/{version_id}/sharing-grants",
    response_model=list[SharingGrantResponse],
)
def list_grants(
    version_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[SharingGrantResponse]:
    version = _id(ResourceKind.ARTIFACT_VERSION, version_id, "artifact_version_not_found")
    with create_session(get_settings().database_url) as session:
        grants = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session)).list_grants(
            claims.principal_id, version
        )
    return [_grant_response(grant) for grant in grants]


@router.delete("/api/v1/sharing-grants/{grant_id}", response_model=SharingGrantResponse)
def revoke_grant(
    grant_id: str,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> SharingGrantResponse:
    parsed = _id(ResourceKind.SHARING_GRANT, grant_id, "sharing_grant_not_found")
    with create_session(get_settings().database_url) as session:
        grant = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session)).revoke(
            claims.principal_id,
            parsed,
            datetime.now(UTC),
            PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return _grant_response(grant)


@router.post(
    "/api/v1/projects/{project_id}/shared-artifact-references",
    response_model=SharedReferenceResponse,
    status_code=201,
)
def create_reference(
    project_id: str,
    body: CreateSharedReferenceRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> SharedReferenceResponse:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    version = _id(
        ResourceKind.ARTIFACT_VERSION,
        body.artifact_version_id,
        "artifact_version_not_found",
    )
    run_id = _id(ResourceKind.RUN, body.run_id, "run_not_found") if body.run_id else None
    with create_session(get_settings().database_url) as session:
        reference = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session)).reference(
            claims.principal_id,
            project,
            version,
            datetime.now(UTC),
            run_id,
            PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return SharedReferenceResponse(
        id=str(reference.id),
        artifact_version_id=str(reference.version_id),
        grant_id=str(reference.grant_id),
        consuming_project_id=str(reference.consuming_project_id),
        run_id=str(reference.run_id) if reference.run_id else None,
        created_at=reference.created_at,
    )


@router.get(
    "/api/v1/projects/{project_id}/shared-artifact-references",
    response_model=list[SharedReferenceResponse],
)
def list_references(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> list[SharedReferenceResponse]:
    project = _id(ResourceKind.PROJECT, project_id, "project_not_found")
    with create_session(get_settings().database_url) as session:
        references = ArtifactSharingService(
            SqlAlchemySharingUnitOfWork(session)
        ).list_references(claims.principal_id, project)
    return [
        SharedReferenceResponse(
            id=str(reference.id),
            artifact_version_id=str(reference.version_id),
            grant_id=str(reference.grant_id),
            consuming_project_id=str(reference.consuming_project_id),
            run_id=str(reference.run_id) if reference.run_id else None,
            created_at=reference.created_at,
        )
        for reference in references
    ]


@router.post(
    "/api/v1/artifact-versions/{derived_version_id}/derivations",
    response_model=DerivationResponse,
    status_code=201,
)
def create_derivation(
    derived_version_id: str,
    body: CreateDerivationRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> DerivationResponse:
    derived = _id(
        ResourceKind.ARTIFACT_VERSION,
        derived_version_id,
        "artifact_version_not_found",
    )
    source = _id(
        ResourceKind.ARTIFACT_VERSION,
        body.source_artifact_version_id,
        "artifact_version_not_found",
    )
    with create_session(get_settings().database_url) as session:
        derivation = ArtifactSharingService(SqlAlchemySharingUnitOfWork(session)).derive(
            claims.principal_id,
            source,
            derived,
            datetime.now(UTC),
            PublicId(ResourceKind.REQUEST, request.state.request_id),
        )
    return DerivationResponse(
        id=str(derivation.id),
        source_artifact_version_id=str(derivation.source_version_id),
        derived_artifact_version_id=str(derivation.derived_version_id),
        created_at=derivation.created_at,
    )
