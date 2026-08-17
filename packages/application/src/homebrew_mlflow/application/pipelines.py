from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    MachineScope,
    PipelineDefinition,
    PipelineVersion,
    ProjectRole,
    PublicId,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict


class PipelineUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def repository_project(self, repository_id: PublicId) -> PublicId | None: ...

    def definition(self, definition_id: PublicId) -> PipelineDefinition | None: ...

    def definition_name_exists(self, project_id: PublicId, name: str) -> bool: ...

    def version_exists(
        self, definition_id: PublicId, repository_id: PublicId, commit: str, path: str
    ) -> bool: ...

    def definitions(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineDefinition, ...]: ...

    def versions(
        self, definition_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineVersion, ...]: ...

    def add_definition(self, definition: PipelineDefinition) -> None: ...

    def add_version(self, version: PipelineVersion) -> None: ...

    def archive_definition(self, definition_id: PublicId, at: datetime) -> None: ...

    def archive_version(self, version_id: PublicId, at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class PipelineSourceReader(Protocol):
    def read(self, repository_id: PublicId, commit: str, path: str) -> bytes: ...


class PipelineService:
    def __init__(
        self, unit_of_work: PipelineUnitOfWork, source_reader: PipelineSourceReader | None = None
    ) -> None:
        self._uow = unit_of_work
        self._source_reader = source_reader

    def list_definitions(
        self, actor_id: PublicId, project_id: PublicId, *, include_archived: bool = False
    ) -> tuple[PipelineDefinition, ...]:
        self._require(actor_id, project_id, MachineScope.READ)
        return self._uow.definitions(project_id, include_archived=include_archived)

    def create_definition(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        name: str,
        request_id: PublicId,
        now: datetime,
    ) -> PipelineDefinition:
        self._require(actor_id, project_id, MachineScope.TRACK)
        if self._uow.definition_name_exists(project_id, name.strip()):
            raise ResourceConflict("Pipeline Definition name already exists")
        definition = PipelineDefinition.create(project_id, name, now)
        self._uow.add_definition(definition)
        self._audit(actor_id, project_id, definition.id, "create", request_id, now)
        self._uow.commit()
        return definition

    def list_versions(
        self, actor_id: PublicId, definition_id: PublicId, *, include_archived: bool = False
    ) -> tuple[PipelineVersion, ...]:
        definition = self._definition(definition_id)
        self._require(actor_id, definition.project_id, MachineScope.READ)
        return self._uow.versions(definition_id, include_archived=include_archived)

    def register_version(
        self,
        actor_id: PublicId,
        definition_id: PublicId,
        repository_id: PublicId,
        git_commit_sha: str,
        pipeline_path: str,
        request_id: PublicId,
        now: datetime,
    ) -> PipelineVersion:
        definition = self._definition(definition_id)
        self._require(actor_id, definition.project_id, MachineScope.TRACK)
        if definition.archived_at is not None:
            raise ResourceConflict("archived Pipeline Definition cannot receive versions")
        if self._uow.repository_project(repository_id) != definition.project_id:
            raise ValueError("Repository must belong to the Pipeline Definition project")
        if self._uow.version_exists(
            definition_id, repository_id, git_commit_sha, pipeline_path
        ):
            raise ResourceConflict("Pipeline Version already exists")
        if self._source_reader is None:
            raise RuntimeError("Pipeline source reader is required to register a version")
        source = self._source_reader.read(repository_id, git_commit_sha, pipeline_path)
        version = PipelineVersion.create(
            definition_id,
            repository_id,
            git_commit_sha,
            pipeline_path,
            hashlib.sha256(source).hexdigest(),
            now,
        )
        self._uow.add_version(version)
        self._audit(actor_id, definition.project_id, version.id, "version", request_id, now)
        self._uow.commit()
        return version

    def archive_definition(
        self,
        actor_id: PublicId,
        definition_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        definition = self._definition(definition_id)
        self._require_maintainer(actor_id, definition.project_id)
        self._uow.archive_definition(definition_id, now)
        self._audit(actor_id, definition.project_id, definition_id, "archive", request_id, now)
        self._uow.commit()

    def archive_version(
        self,
        actor_id: PublicId,
        definition_id: PublicId,
        version_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        definition = self._definition(definition_id)
        self._require_maintainer(actor_id, definition.project_id)
        versions = self._uow.versions(definition_id, include_archived=True)
        if not any(value.id == version_id for value in versions):
            raise ValueError("Pipeline Version does not exist")
        self._uow.archive_version(version_id, now)
        self._audit(actor_id, definition.project_id, version_id, "archive_version", request_id, now)
        self._uow.commit()

    def _definition(self, definition_id: PublicId) -> PipelineDefinition:
        definition = self._uow.definition(definition_id)
        if definition is None:
            raise ValueError("Pipeline Definition does not exist")
        return definition

    def _require(self, actor_id: PublicId, project_id: PublicId, scope: MachineScope) -> None:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, scope):
            raise AuthorizationDenied(f"{scope.value} permission is required")

    def _require_maintainer(self, actor_id: PublicId, project_id: PublicId) -> None:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")

    def _audit(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        resource_id: PublicId,
        action: str,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"pipeline.{action}",
                resource_type="pipeline",
                resource_id=resource_id,
                outcome="success",
                request_id=request_id,
                project_id=project_id,
                occurred_at=now,
            )
        )
