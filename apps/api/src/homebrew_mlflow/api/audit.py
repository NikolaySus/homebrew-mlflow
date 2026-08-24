from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from homebrew_mlflow.application import AccessTokenClaims, AuditEventView, AuditService
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyAuditUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict

from .security import platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/projects", tags=["audit"])


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    occurred_at: datetime
    actor_principal_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    request_id: str
    safe_metadata: dict[str, Any]


class AuditEventPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventResponse]
    total_count: int
    next_before_sequence: int | None


def _response(event: AuditEventView) -> AuditEventResponse:
    return AuditEventResponse(
        sequence=event.sequence,
        occurred_at=(
            event.occurred_at.astimezone(UTC)
            if event.occurred_at.tzinfo is not None
            else event.occurred_at.replace(tzinfo=UTC)
        ),
        actor_principal_id=(
            str(event.actor_principal_id) if event.actor_principal_id is not None else None
        ),
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        outcome=event.outcome,
        request_id=str(event.request_id),
        safe_metadata=event.safe_metadata,
    )


@router.get("/{project_id}/audit-events", response_model=list[AuditEventResponse])
def list_audit_events(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditEventResponse]:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        events = AuditService(SqlAlchemyAuditUnitOfWork(session)).list(
            claims.principal_id,
            project,
            after_sequence=after_sequence,
            limit=limit,
        )
    return [_response(event) for event in events]


@router.get(
    "/{project_id}/audit-events/page", response_model=AuditEventPageResponse
)
def list_recent_audit_events(
    project_id: str,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
    before_sequence: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AuditEventPageResponse:
    try:
        project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    with create_session(get_settings().database_url) as session:
        page = AuditService(SqlAlchemyAuditUnitOfWork(session)).recent_page(
            claims.principal_id,
            project,
            before_sequence=before_sequence,
            limit=limit,
        )
    return AuditEventPageResponse(
        items=[_response(event) for event in page.items],
        total_count=page.total_count,
        next_before_sequence=page.next_before_sequence,
    )
