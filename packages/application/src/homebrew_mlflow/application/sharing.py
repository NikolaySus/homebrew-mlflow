from __future__ import annotations

from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    ArtifactDerivation,
    ArtifactSharingGrant,
    AuditEvent,
    ProjectRole,
    PublicId,
    ResourceKind,
    SharedArtifactReference,
)

from .projects import AuthorizationDenied, ResourceConflict


class SharingUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def version_owner(self, version_id: PublicId) -> PublicId | None: ...

    def project_exists(self, project_id: PublicId) -> bool: ...

    def grant_for_projects(
        self, version_id: PublicId, consuming_project_id: PublicId
    ) -> ArtifactSharingGrant | None: ...

    def grant(self, grant_id: PublicId) -> ArtifactSharingGrant | None: ...

    def grants_for_version(self, version_id: PublicId) -> tuple[ArtifactSharingGrant, ...]: ...

    def add_grant(self, grant: ArtifactSharingGrant) -> None: ...

    def update_grant(self, grant: ArtifactSharingGrant) -> None: ...

    def add_reference(self, reference: SharedArtifactReference) -> None: ...

    def references_for_project(
        self, project_id: PublicId
    ) -> tuple[SharedArtifactReference, ...]: ...

    def version_accessible(self, version_id: PublicId, principal_id: PublicId) -> bool: ...

    def derivation_for_derived(self, version_id: PublicId) -> ArtifactDerivation | None: ...

    def add_derivation(self, derivation: ArtifactDerivation) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class ArtifactSharingService:
    def __init__(self, unit_of_work: SharingUnitOfWork) -> None:
        self._uow = unit_of_work

    def grant(
        self,
        actor_id: PublicId,
        version_id: PublicId,
        consuming_project_id: PublicId,
        now: datetime,
        request_id: PublicId | None = None,
    ) -> ArtifactSharingGrant:
        owner = self._uow.version_owner(version_id)
        if owner is None:
            raise ValueError("Artifact Version does not exist")
        if self._uow.project_role(owner, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to share an Artifact Version")
        if not self._uow.project_exists(consuming_project_id):
            raise ValueError("consuming Research Project does not exist")
        existing = self._uow.grant_for_projects(version_id, consuming_project_id)
        if existing is not None and existing.revoked_at is None:
            return existing
        grant = ArtifactSharingGrant.create(version_id, owner, consuming_project_id, actor_id, now)
        self._uow.add_grant(grant)
        self._audit(
            actor_id,
            owner,
            "artifact_sharing.grant",
            grant.id,
            request_id,
            now,
            {"version_id": str(version_id), "consuming_project_id": str(consuming_project_id)},
        )
        self._uow.commit()
        return grant

    def list_grants(
        self, actor_id: PublicId, version_id: PublicId
    ) -> tuple[ArtifactSharingGrant, ...]:
        owner = self._uow.version_owner(version_id)
        if owner is None:
            raise ValueError("Artifact Version does not exist")
        grants = self._uow.grants_for_version(version_id)
        if self._uow.project_role(owner, actor_id) is not None:
            return grants
        visible = tuple(
            grant
            for grant in grants
            if self._uow.project_role(grant.consuming_project_id, actor_id) is not None
        )
        if not visible:
            raise AuthorizationDenied("Artifact Version is not visible")
        return visible

    def revoke(
        self,
        actor_id: PublicId,
        grant_id: PublicId,
        now: datetime,
        request_id: PublicId | None = None,
    ) -> ArtifactSharingGrant:
        grant = self._uow.grant(grant_id)
        if grant is None:
            raise ValueError("Sharing Grant does not exist")
        if self._uow.project_role(grant.owning_project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to revoke sharing")
        revoked = grant.revoke(now)
        self._uow.update_grant(revoked)
        if grant.revoked_at is None:
            self._audit(
                actor_id,
                grant.owning_project_id,
                "artifact_sharing.revoke",
                grant.id,
                request_id,
                now,
                {"version_id": str(grant.version_id)},
            )
        self._uow.commit()
        return revoked

    def reference(
        self,
        actor_id: PublicId,
        consuming_project_id: PublicId,
        version_id: PublicId,
        now: datetime,
        run_id: PublicId | None = None,
        request_id: PublicId | None = None,
    ) -> SharedArtifactReference:
        role = self._uow.project_role(consuming_project_id, actor_id)
        if role not in {ProjectRole.CONTRIBUTOR, ProjectRole.MAINTAINER}:
            raise AuthorizationDenied("Contributor role is required to reference shared content")
        grant = self._uow.grant_for_projects(version_id, consuming_project_id)
        if grant is None or not grant.permits_new_use(now):
            raise AuthorizationDenied("No active exact-version Sharing Grant exists")
        reference = SharedArtifactReference(
            PublicId.generate(ResourceKind.SHARED_REFERENCE),
            version_id,
            grant.id,
            consuming_project_id,
            actor_id,
            now,
            run_id,
        )
        self._uow.add_reference(reference)
        self._audit(
            actor_id,
            consuming_project_id,
            "artifact_sharing.reference",
            reference.id,
            request_id,
            now,
            {"version_id": str(version_id)},
        )
        self._uow.commit()
        return reference

    def list_references(
        self, actor_id: PublicId, project_id: PublicId
    ) -> tuple[SharedArtifactReference, ...]:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        return self._uow.references_for_project(project_id)

    def derive(
        self,
        actor_id: PublicId,
        source_version_id: PublicId,
        derived_version_id: PublicId,
        now: datetime,
        request_id: PublicId | None = None,
    ) -> ArtifactDerivation:
        derived_owner = self._uow.version_owner(derived_version_id)
        if derived_owner is None:
            raise ValueError("derived Artifact Version does not exist")
        role = self._uow.project_role(derived_owner, actor_id)
        if role not in {ProjectRole.CONTRIBUTOR, ProjectRole.MAINTAINER}:
            raise AuthorizationDenied("Contributor role is required to record derivation")
        if not self._uow.version_accessible(source_version_id, actor_id):
            raise AuthorizationDenied("source Artifact Version is not accessible")
        existing = self._uow.derivation_for_derived(derived_version_id)
        if existing is not None:
            if existing.source_version_id != source_version_id:
                raise ResourceConflict("derived Artifact Version already has another source")
            return existing
        derivation = ArtifactDerivation.create(source_version_id, derived_version_id, actor_id, now)
        self._uow.add_derivation(derivation)
        self._audit(
            actor_id,
            derived_owner,
            "artifact_derivation.create",
            derivation.id,
            request_id,
            now,
            {
                "source_version_id": str(source_version_id),
                "derived_version_id": str(derived_version_id),
            },
        )
        self._uow.commit()
        return derivation

    def _audit(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        action: str,
        resource_id: PublicId,
        request_id: PublicId | None,
        now: datetime,
        metadata: dict[str, str],
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=action,
                resource_type="artifact_sharing",
                resource_id=resource_id,
                outcome="success",
                request_id=request_id or PublicId.generate(ResourceKind.REQUEST),
                project_id=project_id,
                safe_metadata=metadata,
                occurred_at=now,
            )
        )
