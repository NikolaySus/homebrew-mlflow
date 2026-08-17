from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import ArtifactSharingService, AuthorizationDenied
from homebrew_mlflow.domain import (
    ArtifactDerivation,
    ArtifactSharingGrant,
    AuditEvent,
    ProjectRole,
    PublicId,
    ResourceKind,
    SharedArtifactReference,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class SharingStore:
    owner: PublicId
    consumer: PublicId
    maintainer: PublicId
    contributor: PublicId
    version: PublicId
    grants: list[ArtifactSharingGrant] = field(default_factory=list)
    references: list[SharedArtifactReference] = field(default_factory=list)
    derivations: list[ArtifactDerivation] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        if project_id == self.owner and principal_id == self.maintainer:
            return ProjectRole.MAINTAINER
        if project_id == self.consumer and principal_id == self.contributor:
            return ProjectRole.CONTRIBUTOR
        return None

    def version_owner(self, version_id: PublicId) -> PublicId | None:
        return self.owner if version_id == self.version else None

    def project_exists(self, project_id: PublicId) -> bool:
        return project_id in {self.owner, self.consumer}

    def grant_for_projects(
        self, version_id: PublicId, consuming_project_id: PublicId
    ) -> ArtifactSharingGrant | None:
        values = [
            grant
            for grant in self.grants
            if grant.version_id == version_id and grant.consuming_project_id == consuming_project_id
        ]
        return values[-1] if values else None

    def grant(self, grant_id: PublicId) -> ArtifactSharingGrant | None:
        return next((grant for grant in self.grants if grant.id == grant_id), None)

    def add_grant(self, grant: ArtifactSharingGrant) -> None:
        self.grants.append(grant)

    def update_grant(self, grant: ArtifactSharingGrant) -> None:
        self.grants = [grant if item.id == grant.id else item for item in self.grants]

    def add_reference(self, reference: SharedArtifactReference) -> None:
        self.references.append(reference)

    def version_accessible(self, _version_id: PublicId, principal_id: PublicId) -> bool:
        return principal_id in {self.maintainer, self.contributor}

    def derivation_for_derived(self, version_id: PublicId) -> ArtifactDerivation | None:
        return next(
            (item for item in self.derivations if item.derived_version_id == version_id), None
        )

    def add_derivation(self, derivation: ArtifactDerivation) -> None:
        self.derivations.append(derivation)

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        pass


def store() -> SharingStore:
    return SharingStore(
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.PRINCIPAL),
        PublicId.generate(ResourceKind.PRINCIPAL),
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
    )


def test_maintainer_grants_only_one_exact_version_and_contributor_references_it() -> None:
    values = store()
    service = ArtifactSharingService(values)

    grant = service.grant(values.maintainer, values.version, values.consumer, NOW)
    reference = service.reference(
        values.contributor, values.consumer, values.version, NOW + timedelta(seconds=1)
    )

    assert reference.grant_id == grant.id
    assert reference.version_id == values.version


def test_revocation_blocks_new_references_without_rewriting_existing_reference() -> None:
    values = store()
    service = ArtifactSharingService(values)
    grant = service.grant(values.maintainer, values.version, values.consumer, NOW)
    existing = service.reference(
        values.contributor, values.consumer, values.version, NOW + timedelta(seconds=1)
    )

    service.revoke(values.maintainer, grant.id, NOW + timedelta(seconds=2))

    with pytest.raises(AuthorizationDenied):
        service.reference(
            values.contributor, values.consumer, values.version, NOW + timedelta(seconds=3)
        )
    assert values.references == [existing]


def test_contributor_cannot_create_or_revoke_grants() -> None:
    values = store()
    service = ArtifactSharingService(values)

    with pytest.raises(AuthorizationDenied):
        service.grant(values.contributor, values.version, values.consumer, NOW)


def test_modification_records_exact_version_derivation() -> None:
    values = store()
    source = PublicId.generate(ResourceKind.ARTIFACT_VERSION)
    service = ArtifactSharingService(values)

    derivation = service.derive(values.maintainer, source, values.version, NOW)

    assert derivation.source_version_id == source
    assert derivation.derived_version_id == values.version
