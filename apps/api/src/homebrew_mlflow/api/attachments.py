from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from homebrew_mlflow.application import (
    AccessTokenClaims,
    AttachmentService,
    AttachmentUnavailable,
    UploadAttachment,
)
from homebrew_mlflow.domain import MachineScope, PublicId, ResourceKind
from homebrew_mlflow.infrastructure import (
    S3AttachmentObjectStore,
    SqlAlchemyAttachmentUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict

from .security import mlflow_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/runs", tags=["attachments"])


class AttachmentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    is_dir: bool
    file_size: int | None


class AttachmentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[AttachmentFile]


@lru_cache
def attachment_objects(
    endpoint_url: str, bucket: str, access_key_id: str, secret_access_key: str
) -> S3AttachmentObjectStore:
    return S3AttachmentObjectStore(endpoint_url, bucket, access_key_id, secret_access_key)


def _service(session) -> AttachmentService:  # type: ignore[no-untyped-def]
    settings = get_settings()
    objects = attachment_objects(
        settings.s3_endpoint_url,
        settings.attachment_bucket,
        settings.s3_access_key_id,
        settings.s3_secret_access_key.get_secret_value(),
    )
    return AttachmentService(
        SqlAlchemyAttachmentUnitOfWork(session),
        objects,
        max_file_bytes=settings.attachment_max_file_bytes,
        max_run_bytes=settings.attachment_max_run_bytes,
        max_count=settings.attachment_max_count,
    )


def _binding(claims: AccessTokenClaims, run_id: PublicId) -> PublicId:
    if (
        claims.project_id is None
        or claims.run_id != run_id
        or MachineScope.TRACK not in claims.scopes
    ):
        raise HTTPException(status_code=403, detail="run_scope_mismatch")
    return claims.project_id


def _run_id(value: str) -> PublicId:
    try:
        return PublicId(ResourceKind.RUN, value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="run_not_found") from error


@router.post("/{run_id}/attachments", status_code=201)
def upload_attachment(
    run_id: str,
    path: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    claims: Annotated[AccessTokenClaims, Depends(mlflow_claims)],
) -> AttachmentFile:
    parsed_run = _run_id(run_id)
    project_id = _binding(claims, parsed_run)
    content = file.file.read(get_settings().attachment_max_file_bytes + 1)
    with create_session(get_settings().database_url) as session:
        try:
            attachment = _service(session).upload(
                claims.principal_id,
                UploadAttachment(
                    parsed_run,
                    project_id,
                    path,
                    content,
                    file.content_type or "application/octet-stream",
                    datetime.now(UTC),
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid_attachment") from error
    return AttachmentFile(path=attachment.path, is_dir=False, file_size=attachment.size)


@router.get("/{run_id}/attachments", response_model=AttachmentListResponse)
def list_attachments(
    run_id: str,
    claims: Annotated[AccessTokenClaims, Depends(mlflow_claims)],
    path: Annotated[str, Query()] = "",
) -> AttachmentListResponse:
    parsed_run = _run_id(run_id)
    project_id = _binding(claims, parsed_run)
    with create_session(get_settings().database_url) as session:
        attachments = _service(session).list(claims.principal_id, parsed_run, project_id)
    prefix = f"{path.rstrip('/')}/" if path else ""
    files: dict[str, AttachmentFile] = {}
    for attachment in attachments:
        if not attachment.path.startswith(prefix):
            continue
        remainder = attachment.path.removeprefix(prefix)
        head, separator, _tail = remainder.partition("/")
        visible_path = f"{prefix}{head}"
        files[visible_path] = AttachmentFile(
            path=visible_path,
            is_dir=bool(separator),
            file_size=None if separator else attachment.size,
        )
    return AttachmentListResponse(files=sorted(files.values(), key=lambda item: item.path))


@router.get("/{run_id}/attachments/content")
def download_attachment(
    run_id: str,
    path: Annotated[str, Query(min_length=1)],
    claims: Annotated[AccessTokenClaims, Depends(mlflow_claims)],
) -> Response:
    parsed_run = _run_id(run_id)
    project_id = _binding(claims, parsed_run)
    with create_session(get_settings().database_url) as session:
        try:
            attachment, content = _service(session).download(
                claims.principal_id, parsed_run, project_id, path
            )
        except AttachmentUnavailable as error:
            raise HTTPException(status_code=410, detail="attachment_expired") from error
        except ValueError as error:
            raise HTTPException(status_code=404, detail="attachment_not_found") from error
    return Response(content=content, media_type=attachment.media_type)
