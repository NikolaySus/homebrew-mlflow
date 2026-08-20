from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from homebrew_mlflow.domain import (
    ArtifactKind,
    AuditEvent,
    MachineScope,
    ProjectRole,
    PublicationEvent,
    PublicationOperation,
    PublicId,
    ResourceKind,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict


class PublicationUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def artifact_kind(self, project_id: PublicId, artifact_id: PublicId) -> ArtifactKind | None: ...

    def find_by_idempotency_key(
        self, project_id: PublicId, key: str
    ) -> PublicationOperation | None: ...

    def add_operation(self, operation: PublicationOperation) -> None: ...

    def add_event(self, event: PublicationEvent) -> None: ...

    def operation(self, operation_id: PublicId) -> PublicationOperation | None: ...

    def events_after(
        self, operation_id: PublicId, sequence: int
    ) -> tuple[PublicationEvent, ...]: ...

    def event_history_expired_through(self, operation_id: PublicId) -> int: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PublicationCreation:
    operation: PublicationOperation
    replayed: bool


class EventHistoryExpired(ValueError):
    pass


class PublicationService:
    def __init__(self, unit_of_work: PublicationUnitOfWork) -> None:
        self._uow = unit_of_work

    def create(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        idempotency_key: str,
        request: dict[str, Any],
        occurred_at: datetime,
        request_id: PublicId | None = None,
    ) -> PublicationCreation:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.PUBLISH):
            raise AuthorizationDenied("Contributor role is required to publish")
        try:
            artifact_id = PublicId(ResourceKind.ARTIFACT, str(request.get("artifact_id")))
        except ValueError as error:
            raise ValueError("Artifact does not exist") from error
        artifact_kind = self._uow.artifact_kind(project_id, artifact_id)
        if artifact_kind is None:
            raise ValueError("Artifact does not exist")
        signature = request.get("model_signature")
        if artifact_kind is ArtifactKind.MODEL and signature is None:
            raise ResourceConflict("model publications require a committed signature")
        if artifact_kind is not ArtifactKind.MODEL and signature is not None:
            raise ResourceConflict("only model publications accept a signature")
        if artifact_kind is ArtifactKind.MODEL and (
            not isinstance(signature, dict)
            or set(signature) != {"path", "format"}
            or not isinstance(signature.get("path"), str)
            or not signature["path"]
            or len(signature["path"]) > 500
            or signature.get("format") != "homebrew-mlflow-signature-v1"
        ):
            raise ResourceConflict("model signature reference is invalid")
        normalized = json.dumps(
            request, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        request_digest = hashlib.sha256(normalized).hexdigest()
        existing = self._uow.find_by_idempotency_key(project_id, idempotency_key)
        if existing is not None:
            if existing.request_digest != request_digest:
                raise ResourceConflict("idempotency key was used with a different request")
            return PublicationCreation(existing, replayed=True)

        operation = PublicationOperation.queued(
            project_id, idempotency_key, request_digest, request, actor_id
        )
        self._uow.add_operation(operation)
        self._uow.add_event(PublicationEvent(operation.id, 1, "operation.queued", occurred_at))
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action="publication.create",
                resource_type="publication_operation",
                resource_id=operation.id,
                outcome="success",
                request_id=request_id or PublicId.generate(ResourceKind.REQUEST),
                project_id=project_id,
                safe_metadata={"request_digest": request_digest},
                occurred_at=occurred_at,
            )
        )
        self._uow.commit()
        return PublicationCreation(operation, replayed=False)

    def get(self, actor_id: PublicId, operation_id: PublicId) -> PublicationOperation:
        operation = self._uow.operation(operation_id)
        if operation is None:
            raise ValueError("publication operation does not exist")
        role = self._uow.project_role(operation.project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return operation

    def events(
        self, actor_id: PublicId, operation_id: PublicId, after: int
    ) -> tuple[PublicationEvent, ...]:
        self.get(actor_id, operation_id)
        if after < self._uow.event_history_expired_through(operation_id):
            raise EventHistoryExpired("requested publication event history has expired")
        return self._uow.events_after(operation_id, after)
