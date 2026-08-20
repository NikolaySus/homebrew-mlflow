from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from homebrew_mlflow.application import (
    AccessTokenClaims,
    EventHistoryExpired,
    PublicationService,
)
from homebrew_mlflow.contracts import ModelSignatureReference
from homebrew_mlflow.domain import PublicationEvent, PublicationState, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import SqlAlchemyPublicationUnitOfWork, create_session
from pydantic import BaseModel, ConfigDict, Field

from .security import publication_claims
from .settings import get_settings

router = APIRouter(tags=["publications"])


class PipelineSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pipeline-output"]
    pipeline_file: str = Field(min_length=1, max_length=500)
    stage: str = Field(min_length=1, max_length=250)
    output: str = Field(min_length=1, max_length=1000)


class StandaloneSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["standalone-output"]
    dvc_file: str = Field(min_length=1, max_length=500)
    output: str = Field(min_length=1, max_length=1000)


class PublicationClient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)


class CreatePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    repository_id: str
    commit_sha: str = Field(pattern="^[0-9a-f]{40}$")
    selector: PipelineSelector | StandaloneSelector = Field(discriminator="kind")
    run_id: str | None = None
    model_signature: ModelSignatureReference | None = None
    client: PublicationClient


class PublicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    project_id: str
    state: str
    status_url: str
    events_url: str


def _response(operation) -> PublicationResponse:  # type: ignore[no-untyped-def]
    base = f"/api/v1/publication-operations/{operation.id}"
    return PublicationResponse(
        operation_id=str(operation.id),
        project_id=str(operation.project_id),
        state=operation.state.value,
        status_url=base,
        events_url=f"{base}/events",
    )


def _operation_id(value: str) -> PublicId:
    try:
        return PublicId(ResourceKind.PUBLICATION, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="publication_not_found") from error


@router.post(
    "/api/v1/projects/{project_id}/publication-operations",
    response_model=PublicationResponse,
    status_code=202,
)
def create_publication(
    project_id: str,
    body: CreatePublicationRequest,
    claims: Annotated[AccessTokenClaims, Depends(publication_claims)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    response: Response,
    http_request: Request,
) -> PublicationResponse:
    try:
        parsed_project = PublicId(ResourceKind.PROJECT, project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    if claims.project_id != parsed_project:
        raise HTTPException(status_code=403, detail="project_scope_mismatch")
    with create_session(get_settings().database_url) as session:
        creation = PublicationService(SqlAlchemyPublicationUnitOfWork(session)).create(
            claims.principal_id,
            parsed_project,
            idempotency_key,
            body.model_dump(mode="json"),
            datetime.now(UTC),
            PublicId(ResourceKind.REQUEST, http_request.state.request_id),
        )
    response.headers["Location"] = f"/api/v1/publication-operations/{creation.operation.id}"
    return _response(creation.operation)


@router.get("/api/v1/publication-operations/{operation_id}", response_model=PublicationResponse)
def get_publication(
    operation_id: str,
    claims: Annotated[AccessTokenClaims, Depends(publication_claims)],
) -> PublicationResponse:
    parsed = _operation_id(operation_id)
    with create_session(get_settings().database_url) as session:
        try:
            operation = PublicationService(SqlAlchemyPublicationUnitOfWork(session)).get(
                claims.principal_id, parsed
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="publication_not_found") from error
    if claims.project_id != operation.project_id:
        raise HTTPException(status_code=403, detail="project_scope_mismatch")
    return _response(operation)


def _sse(event: PublicationEvent) -> str:
    data = {
        "operation_id": str(event.operation_id),
        "occurred_at": event.occurred_at.isoformat(),
        **event.payload,
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.name}\n"
        f"data: {json.dumps(data, separators=(',', ':'), sort_keys=True)}\n\n"
    )


@router.get("/api/v1/publication-operations/{operation_id}/events")
def publication_events(
    operation_id: str,
    claims: Annotated[AccessTokenClaims, Depends(publication_claims)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    parsed = _operation_id(operation_id)
    try:
        after = int(last_event_id) if last_event_id is not None else 0
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid_last_event_id") from error
    if after < 0:
        raise HTTPException(status_code=400, detail="invalid_last_event_id")
    with create_session(get_settings().database_url) as session:
        service = PublicationService(SqlAlchemyPublicationUnitOfWork(session))
        operation = service.get(claims.principal_id, parsed)
        try:
            service.events(claims.principal_id, parsed, after)
        except EventHistoryExpired as error:
            raise HTTPException(status_code=410, detail="event_history_expired") from error
    if claims.project_id != operation.project_id:
        raise HTTPException(status_code=403, detail="project_scope_mismatch")

    def stream() -> Iterator[str]:
        cursor = after
        last_activity = time.monotonic()
        while True:
            with create_session(get_settings().database_url) as session:
                service = PublicationService(SqlAlchemyPublicationUnitOfWork(session))
                events = service.events(claims.principal_id, parsed, cursor)
                current = service.get(claims.principal_id, parsed)
            for event in events:
                cursor = event.sequence
                last_activity = time.monotonic()
                yield _sse(event)
            if current.state in {PublicationState.PUBLISHED, PublicationState.FAILED}:
                return
            if time.monotonic() - last_activity >= 15:
                last_activity = time.monotonic()
                yield ": heartbeat\n\n"
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")
