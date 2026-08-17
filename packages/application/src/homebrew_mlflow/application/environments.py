from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    EnvironmentKind,
    EnvironmentSpecification,
    MachineScope,
    ProjectRole,
    PublicId,
    ResourceKind,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict
from .redaction import redact_mapping


class EnvironmentUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def name_exists(self, project_id: PublicId, name: str) -> bool: ...

    def specifications(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[EnvironmentSpecification, ...]: ...

    def specification(self, specification_id: PublicId) -> EnvironmentSpecification | None: ...

    def add(self, specification: EnvironmentSpecification) -> None: ...

    def archive(self, specification_id: PublicId, at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class EnvironmentService:
    def __init__(self, unit_of_work: EnvironmentUnitOfWork) -> None:
        self._uow = unit_of_work

    def list(
        self, actor_id: PublicId, project_id: PublicId, *, include_archived: bool = False
    ) -> tuple[EnvironmentSpecification, ...]:
        self._require(actor_id, project_id, MachineScope.READ)
        return self._uow.specifications(project_id, include_archived=include_archived)

    def create(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        name: str,
        kind: EnvironmentKind,
        document: dict[str, Any],
        request_id: PublicId,
        now: datetime,
    ) -> EnvironmentSpecification:
        self._require(actor_id, project_id, MachineScope.TRACK)
        normalized_name = name.strip()
        if self._uow.name_exists(project_id, normalized_name):
            raise ResourceConflict("Environment Specification name already exists")
        if redact_mapping(document) != document:
            raise ValueError("Environment Specification contains sensitive fields")
        canonical = json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        specification = EnvironmentSpecification(
            PublicId.generate(ResourceKind.ENVIRONMENT_SPECIFICATION),
            project_id,
            normalized_name,
            kind,
            canonical,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            now,
        )
        self._uow.add(specification)
        self._audit(actor_id, specification, "create", request_id, now)
        self._uow.commit()
        return specification

    def archive(
        self,
        actor_id: PublicId,
        specification_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> EnvironmentSpecification:
        value = self._uow.specification(specification_id)
        if value is None:
            raise ValueError("Environment Specification does not exist")
        if self._uow.project_role(value.project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        self._uow.archive(specification_id, now)
        self._audit(actor_id, value, "archive", request_id, now)
        self._uow.commit()
        return self._uow.specification(specification_id) or value

    def _require(self, actor_id: PublicId, project_id: PublicId, scope: MachineScope) -> None:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, scope):
            raise AuthorizationDenied(f"{scope.value} permission is required")

    def _audit(
        self,
        actor_id: PublicId,
        specification: EnvironmentSpecification,
        action: str,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"environment.{action}",
                resource_type="environment_specification",
                resource_id=specification.id,
                outcome="success",
                request_id=request_id,
                project_id=specification.project_id,
                safe_metadata={"kind": specification.kind.value, "sha256": specification.sha256},
                occurred_at=now,
            )
        )
