from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    MachineScope,
    ProjectRole,
    PublicId,
    Run,
    RunAttachment,
    RunState,
    normalize_artifact_path,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_RUN_ATTACHMENT_BYTES = 250 * 1024 * 1024
MAX_RUN_ATTACHMENTS = 1000


class AttachmentUnavailable(ValueError):
    pass


class AttachmentUnitOfWork(Protocol):
    def run(self, run_id: PublicId) -> Run | None: ...

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def attachment(self, run_id: PublicId, path: str) -> RunAttachment | None: ...

    def list_attachments(self, run_id: PublicId) -> tuple[RunAttachment, ...]: ...

    def attachment_totals(self, run_id: PublicId) -> tuple[int, int]: ...

    def add_attachment(self, attachment: RunAttachment) -> None: ...

    def commit(self) -> None: ...


class AttachmentObjectStore(Protocol):
    def put(self, object_key: str, content: bytes, media_type: str) -> None: ...

    def get(self, object_key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class UploadAttachment:
    run_id: PublicId
    project_id: PublicId
    path: str
    content: bytes
    media_type: str
    occurred_at: datetime


class AttachmentService:
    def __init__(
        self,
        unit_of_work: AttachmentUnitOfWork,
        objects: AttachmentObjectStore,
        *,
        max_file_bytes: int = MAX_ATTACHMENT_BYTES,
        max_run_bytes: int = MAX_RUN_ATTACHMENT_BYTES,
        max_count: int = MAX_RUN_ATTACHMENTS,
    ) -> None:
        self._uow = unit_of_work
        self._objects = objects
        self._max_file_bytes = max_file_bytes
        self._max_run_bytes = max_run_bytes
        self._max_count = max_count

    def upload(self, actor_id: PublicId, command: UploadAttachment) -> RunAttachment:
        run = self._authorized_run(
            actor_id, command.run_id, command.project_id, MachineScope.TRACK
        )
        if run.state is not RunState.RUNNING:
            raise ResourceConflict("only a running Run accepts attachments")
        path = normalize_artifact_path(command.path)
        size = len(command.content)
        if size > self._max_file_bytes:
            raise ValueError("attachment exceeds the per-file limit")
        if not self._allowed_media_type(command.media_type):
            raise ValueError("attachment media type is not permitted")
        digest = hashlib.sha256(command.content).hexdigest()
        existing = self._uow.attachment(run.id, path)
        if existing is not None:
            if existing.sha256 != digest:
                raise ResourceConflict("attachment paths are immutable within a Run")
            return existing
        count, total_size = self._uow.attachment_totals(run.id)
        if count >= self._max_count or total_size + size > self._max_run_bytes:
            raise ValueError("Run attachment quota exceeded")
        object_key = f"run-attachments/{run.project_id}/{run.id}/{digest}"
        attachment = RunAttachment(
            run.id,
            path,
            size,
            command.media_type,
            digest,
            object_key,
            command.occurred_at,
        )
        self._objects.put(object_key, command.content, command.media_type)
        self._uow.add_attachment(attachment)
        self._uow.commit()
        return attachment

    def list(
        self, actor_id: PublicId, run_id: PublicId, project_id: PublicId
    ) -> tuple[RunAttachment, ...]:
        self._authorized_run(actor_id, run_id, project_id, MachineScope.READ)
        return self._uow.list_attachments(run_id)

    def download(
        self, actor_id: PublicId, run_id: PublicId, project_id: PublicId, path: str
    ) -> tuple[RunAttachment, bytes]:
        self._authorized_run(actor_id, run_id, project_id, MachineScope.READ)
        attachment = self._uow.attachment(run_id, normalize_artifact_path(path))
        if attachment is None:
            raise ValueError("attachment does not exist")
        if attachment.purged_at is not None:
            raise AttachmentUnavailable("attachment bytes expired under retention policy")
        return attachment, self._objects.get(attachment.object_key)

    def _authorized_run(
        self,
        actor_id: PublicId,
        run_id: PublicId,
        project_id: PublicId,
        requirement: MachineScope,
    ) -> Run:
        run = self._uow.run(run_id)
        if run is None:
            raise ValueError("Run does not exist")
        if run.project_id != project_id:
            raise AuthorizationDenied("attachment credential is bound to another project")
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, requirement):
            raise AuthorizationDenied("project role does not permit Run attachment access")
        return run

    @staticmethod
    def _allowed_media_type(media_type: str) -> bool:
        return media_type.startswith("text/") or media_type in {
            "application/json",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/svg+xml",
        }
