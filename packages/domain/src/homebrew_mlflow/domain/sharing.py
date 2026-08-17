from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .identifiers import PublicId, ResourceKind


@dataclass(frozen=True, slots=True)
class ArtifactSharingGrant:
    id: PublicId
    version_id: PublicId
    owning_project_id: PublicId
    consuming_project_id: PublicId
    created_at: datetime
    effective_at: datetime
    created_by: PublicId
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.SHARING_GRANT:
            raise ValueError("invalid sharing grant identifier")
        if self.version_id.kind is not ResourceKind.ARTIFACT_VERSION:
            raise ValueError("sharing requires an exact Artifact Version")
        if (
            self.owning_project_id.kind is not ResourceKind.PROJECT
            or self.consuming_project_id.kind is not ResourceKind.PROJECT
        ):
            raise ValueError("sharing grant projects must be Research Projects")
        if self.owning_project_id == self.consuming_project_id:
            raise ValueError("cross-project grant requires different projects")
        if self.revoked_at is not None and self.revoked_at < self.effective_at:
            raise ValueError("revocation cannot precede grant effectiveness")
        if self.created_by.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("sharing grant actor must be a Principal")

    @classmethod
    def create(
        cls,
        version_id: PublicId,
        owning_project_id: PublicId,
        consuming_project_id: PublicId,
        actor_id: PublicId,
        now: datetime,
    ) -> ArtifactSharingGrant:
        return cls(
            PublicId.generate(ResourceKind.SHARING_GRANT),
            version_id,
            owning_project_id,
            consuming_project_id,
            now,
            now,
            actor_id,
        )

    def revoke(self, at: datetime) -> ArtifactSharingGrant:
        if self.revoked_at is not None:
            return self
        if at < self.effective_at:
            raise ValueError("revocation cannot precede grant effectiveness")
        return replace(self, revoked_at=at)

    def permits_new_use(self, at: datetime) -> bool:
        return at >= self.effective_at and (self.revoked_at is None or at < self.revoked_at)

    def permits_completed_run_recovery(self, completed_at: datetime) -> bool:
        return self.revoked_at is None or completed_at < self.revoked_at


@dataclass(frozen=True, slots=True)
class SharedArtifactReference:
    id: PublicId
    version_id: PublicId
    grant_id: PublicId
    consuming_project_id: PublicId
    created_by: PublicId
    created_at: datetime
    run_id: PublicId | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.SHARED_REFERENCE:
            raise ValueError("invalid shared reference identifier")
        if self.version_id.kind is not ResourceKind.ARTIFACT_VERSION:
            raise ValueError("shared reference requires an exact Artifact Version")
        if self.grant_id.kind is not ResourceKind.SHARING_GRANT:
            raise ValueError("shared reference requires a Sharing Grant")
        if self.consuming_project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("shared reference must belong to a Research Project")
        if self.created_by.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("shared reference actor must be a Principal")
        if self.run_id is not None and self.run_id.kind is not ResourceKind.RUN:
            raise ValueError("shared reference Run identifier is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactDerivation:
    id: PublicId
    source_version_id: PublicId
    derived_version_id: PublicId
    created_by: PublicId
    created_at: datetime

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.DERIVATION:
            raise ValueError("invalid derivation identifier")
        if (
            self.source_version_id.kind is not ResourceKind.ARTIFACT_VERSION
            or self.derived_version_id.kind is not ResourceKind.ARTIFACT_VERSION
        ):
            raise ValueError("derivation must connect exact Artifact Versions")
        if self.source_version_id == self.derived_version_id:
            raise ValueError("an Artifact Version cannot derive from itself")
        if self.created_by.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("derivation actor must be a Principal")

    @classmethod
    def create(
        cls,
        source_version_id: PublicId,
        derived_version_id: PublicId,
        actor_id: PublicId,
        now: datetime,
    ) -> ArtifactDerivation:
        return cls(
            PublicId.generate(ResourceKind.DERIVATION),
            source_version_id,
            derived_version_id,
            actor_id,
            now,
        )
