from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    MachineCredentialService,
    StoredMachineCredential,
)
from homebrew_mlflow.domain import (
    AuditEvent,
    MachineScope,
    Principal,
    ProjectMembership,
    ProjectRole,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, tzinfo=UTC)


@dataclass
class Store:
    project: PublicId
    maintainer: PublicId
    record: StoredMachineCredential | None = None
    audits: list[AuditEvent] = field(default_factory=list)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        if project_id == self.project and principal_id == self.maintainer:
            return ProjectRole.MAINTAINER
        if self.record and project_id == self.project and principal_id == self.record.principal_id:
            return ProjectRole.CONTRIBUTOR
        return None

    def add_machine_credential(
        self,
        principal: Principal,
        _membership: ProjectMembership,
        credential_id: PublicId,
        digest: str,
        scopes: frozenset[MachineScope],
        _created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.record = StoredMachineCredential(
            credential_id, principal.id, self.project, digest, scopes, expires_at, False
        )

    def machine_credential(self, _credential_id: PublicId) -> StoredMachineCredential | None:
        return self.record

    def machine_credentials(self, _project_id: PublicId) -> tuple[StoredMachineCredential, ...]:
        return (self.record,) if self.record is not None else ()

    def revoke_machine_credential(
        self, _credential_id: PublicId, _revoked_at: datetime
    ) -> None:
        if self.record is not None:
            self.record = StoredMachineCredential(
                self.record.id,
                self.record.principal_id,
                self.record.project_id,
                self.record.digest,
                self.record.scopes,
                self.record.expires_at,
                True,
            )

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        pass


def test_machine_credential_is_one_project_role_intersected_with_scopes() -> None:
    project = PublicId.generate(ResourceKind.PROJECT)
    maintainer = PublicId.generate(ResourceKind.PRINCIPAL)
    store = Store(project, maintainer)
    service = MachineCredentialService(store)

    created = service.create(
        maintainer,
        project,
        "CI publisher",
        ProjectRole.CONTRIBUTOR,
        frozenset({MachineScope.READ, MachineScope.PUBLISH}),
        NOW,
    )
    authenticated = service.authenticate(created.id, created.secret)

    assert authenticated.project_id == project
    assert authenticated.scopes == frozenset({MachineScope.READ, MachineScope.PUBLISH})
    assert authenticated.expires_at == NOW + timedelta(days=90)


def test_viewer_machine_cannot_receive_publish_scope() -> None:
    project = PublicId.generate(ResourceKind.PROJECT)
    maintainer = PublicId.generate(ResourceKind.PRINCIPAL)

    with pytest.raises(ValueError, match="exceed"):
        MachineCredentialService(Store(project, maintainer)).create(
            maintainer,
            project,
            "Reader",
            ProjectRole.VIEWER,
            frozenset({MachineScope.PUBLISH}),
            NOW,
        )


def test_wrong_machine_secret_is_rejected() -> None:
    project = PublicId.generate(ResourceKind.PROJECT)
    maintainer = PublicId.generate(ResourceKind.PRINCIPAL)
    store = Store(project, maintainer)
    service = MachineCredentialService(store)
    created = service.create(
        maintainer,
        project,
        "CI",
        ProjectRole.CONTRIBUTOR,
        frozenset({MachineScope.TRACK}),
        NOW,
    )

    with pytest.raises(AuthorizationDenied):
        service.authenticate(created.id, "wrong")


def test_expired_machine_secret_is_rejected() -> None:
    project = PublicId.generate(ResourceKind.PROJECT)
    maintainer = PublicId.generate(ResourceKind.PRINCIPAL)
    store = Store(project, maintainer)
    service = MachineCredentialService(store)
    created = service.create(
        maintainer,
        project,
        "CI",
        ProjectRole.CONTRIBUTOR,
        frozenset({MachineScope.TRACK}),
        NOW,
    )

    with pytest.raises(AuthorizationDenied):
        service.authenticate(created.id, created.secret, NOW + timedelta(days=90))
