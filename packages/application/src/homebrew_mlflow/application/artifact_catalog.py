from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    Artifact,
    ArtifactDerivation,
    ArtifactVersion,
    AuditEvent,
    MachineScope,
    ProjectRole,
    PublicId,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict
from .publication_worker import ValidatedFile
from .retention import RetentionDependencies


class ArtifactCatalogUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def artifact_by_name(self, project_id: PublicId, name: str) -> Artifact | None: ...

    def artifact(self, artifact_id: PublicId) -> Artifact | None: ...

    def add_artifact(self, artifact: Artifact) -> None: ...

    def artifacts(self, project_id: PublicId) -> tuple[Artifact, ...]: ...

    def version(self, version_id: PublicId) -> ArtifactVersion | None: ...

    def versions(self, artifact_id: PublicId) -> tuple[ArtifactVersion, ...]: ...

    def version_files(self, version_id: PublicId) -> tuple[ValidatedFile, ...]: ...

    def version_accessible(
        self,
        version_id: PublicId,
        principal_id: PublicId,
        recovery_run_id: PublicId | None = None,
    ) -> bool: ...

    def version_metadata_accessible(
        self, version_id: PublicId, principal_id: PublicId
    ) -> bool: ...

    def derivations(self, version_id: PublicId) -> tuple[ArtifactDerivation, ...]: ...

    def pointer_output_path(self, version_id: PublicId) -> str | None: ...

    def retention_dependencies(self, version_id: PublicId) -> RetentionDependencies: ...

    def archive_artifact(self, artifact_id: PublicId, at: datetime) -> None: ...

    def archive_version(self, version_id: PublicId, at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DvcPointer:
    version: ArtifactVersion
    output_path: str

    def content(self) -> str:
        identity = self.version.identity
        digest = identity.digest + (".dir" if identity.kind.value == "directory" else "")
        lines = [
            "outs:",
            f"- {identity.algorithm}: {digest}",
            f"  hash: {identity.algorithm}",
            f"  size: {identity.size}",
        ]
        if identity.kind.value == "directory":
            lines.append(f"  nfiles: {identity.file_count}")
        escaped_path = self.output_path.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  path: "{escaped_path}"')
        return "\n".join(lines) + "\n"


class ArtifactCatalogService:
    def __init__(self, unit_of_work: ArtifactCatalogUnitOfWork) -> None:
        self._uow = unit_of_work

    def create(
        self, actor_id: PublicId, project_id: PublicId, name: str, occurred_at: datetime
    ) -> Artifact:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.PUBLISH):
            raise AuthorizationDenied("Contributor role is required to create an Artifact")
        normalized = name.strip()
        if self._uow.artifact_by_name(project_id, normalized) is not None:
            raise ResourceConflict("Artifact name already exists in the project")
        artifact = Artifact.create(project_id, normalized, occurred_at)
        self._uow.add_artifact(artifact)
        self._uow.commit()
        return artifact

    def list_artifacts(self, actor_id: PublicId, project_id: PublicId) -> tuple[Artifact, ...]:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return self._uow.artifacts(project_id)

    def list_versions(
        self, actor_id: PublicId, artifact_id: PublicId
    ) -> tuple[ArtifactVersion, ...]:
        versions = self._uow.versions(artifact_id)
        return tuple(
            version
            for version in versions
            if version.archived_at is None
            and self._uow.version_accessible(version.id, actor_id)
        )

    def get_version(
        self,
        actor_id: PublicId,
        version_id: PublicId,
        recovery_run_id: PublicId | None = None,
    ) -> ArtifactVersion:
        version = self._uow.version(version_id)
        if version is None or not self._uow.version_metadata_accessible(version_id, actor_id):
            raise ValueError("Artifact Version does not exist")
        return version

    def files(
        self,
        actor_id: PublicId,
        version_id: PublicId,
        recovery_run_id: PublicId | None = None,
    ) -> tuple[ValidatedFile, ...]:
        if not self._uow.version_accessible(version_id, actor_id, recovery_run_id):
            raise ValueError("Artifact Version bytes are not accessible")
        return self._uow.version_files(version_id)

    def lineage(
        self, actor_id: PublicId, version_id: PublicId
    ) -> tuple[ArtifactDerivation, ...]:
        self.get_version(actor_id, version_id)
        return self._uow.derivations(version_id)

    def pointer(
        self,
        actor_id: PublicId,
        version_id: PublicId,
        recovery_run_id: PublicId | None = None,
    ) -> DvcPointer:
        version = self._uow.version(version_id)
        if version is None or not self._uow.version_accessible(
            version_id, actor_id, recovery_run_id
        ):
            raise ValueError("Artifact Version bytes are not accessible")
        output_path = self._uow.pointer_output_path(version_id)
        if output_path is None:
            raise ValueError("Artifact Version has no publication selector")
        return DvcPointer(version, output_path)

    def dependencies(
        self, actor_id: PublicId, version_id: PublicId
    ) -> RetentionDependencies:
        self.get_version(actor_id, version_id)
        return self._uow.retention_dependencies(version_id)

    def archive_artifact(
        self,
        actor_id: PublicId,
        artifact_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> Artifact:
        artifact = self._uow.artifact(artifact_id)
        if artifact is None:
            raise ValueError("Artifact does not exist")
        self._require_maintainer(actor_id, artifact.owning_project_id)
        self._uow.archive_artifact(artifact_id, now)
        self._audit(actor_id, artifact.owning_project_id, artifact_id, "artifact", request_id, now)
        self._uow.commit()
        return self._uow.artifact(artifact_id) or artifact

    def archive_version(
        self,
        actor_id: PublicId,
        version_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> ArtifactVersion:
        version = self._uow.version(version_id)
        if version is None:
            raise ValueError("Artifact Version does not exist")
        self._require_maintainer(actor_id, version.owning_project_id)
        self._uow.archive_version(version_id, now)
        self._audit(actor_id, version.owning_project_id, version_id, "version", request_id, now)
        self._uow.commit()
        return self._uow.version(version_id) or version

    def _require_maintainer(self, actor_id: PublicId, project_id: PublicId) -> None:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")

    def _audit(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        resource_id: PublicId,
        resource_kind: str,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"retention.archive_{resource_kind}",
                resource_type=f"artifact_{resource_kind}",
                resource_id=resource_id,
                outcome="success",
                request_id=request_id,
                project_id=project_id,
                safe_metadata={},
                occurred_at=now,
            )
        )
