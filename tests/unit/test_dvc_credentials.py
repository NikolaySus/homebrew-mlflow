from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    DvcCredentialService,
    TemporaryS3Credential,
)
from homebrew_mlflow.domain import AuditEvent, ProjectRole, PublicId, ResourceKind


@dataclass
class Authorization:
    actor_id: PublicId
    project_id: PublicId
    role: ProjectRole | None

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        if project_id == self.project_id and principal_id == self.actor_id:
            return self.role
        return None

    def shared_dvc_read_keys(
        self,
        _project_id: PublicId,
        _principal_id: PublicId,
        _recovery_run_id: PublicId | None,
        _at: datetime,
    ) -> tuple[str, ...]:
        return ("dvc/owner/files/md5/aa/bb",)


@dataclass
class Issuer:
    credential: TemporaryS3Credential
    issued_for: PublicId | None = None
    read_keys: tuple[str, ...] = ()

    def issue(
        self, project_id: PublicId, read_only_object_keys: tuple[str, ...]
    ) -> TemporaryS3Credential:
        self.issued_for = project_id
        self.read_keys = read_only_object_keys
        return self.credential


@dataclass
class Audit:
    events: list[AuditEvent]
    committed: bool = False

    def append_audit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def commit(self) -> None:
        self.committed = True


def test_dvc_credential_issuance_requires_current_contributor_membership() -> None:
    actor = PublicId.generate(ResourceKind.PRINCIPAL)
    project = PublicId.generate(ResourceKind.PROJECT)
    credential = TemporaryS3Credential(
        "temporary-access",
        "temporary-secret",
        "temporary-session",
        datetime.now(UTC) + timedelta(minutes=15),
    )
    issuer = Issuer(credential)

    assert (
        DvcCredentialService(Authorization(actor, project, ProjectRole.CONTRIBUTOR), issuer).issue(
            actor, project
        )
        == credential
    )
    assert issuer.issued_for == project
    assert issuer.read_keys == ("dvc/owner/files/md5/aa/bb",)
    with pytest.raises(AuthorizationDenied):
        DvcCredentialService(Authorization(actor, project, None), issuer).issue(actor, project)


def test_dvc_credential_issuance_is_audited_without_secret_material() -> None:
    actor = PublicId.generate(ResourceKind.PRINCIPAL)
    project = PublicId.generate(ResourceKind.PROJECT)
    request = PublicId.generate(ResourceKind.REQUEST)
    credential = TemporaryS3Credential(
        "access-key",
        "secret-key",
        "session-token",
        datetime.now(UTC) + timedelta(minutes=15),
    )
    audit = Audit([])

    DvcCredentialService(
        Authorization(actor, project, ProjectRole.CONTRIBUTOR), Issuer(credential), audit
    ).issue(actor, project, request_id=request)

    assert audit.committed
    assert audit.events[0].action == "dvc_credentials.issue"
    assert "secret-key" not in repr(audit.events[0].safe_metadata)
    assert "session-token" not in repr(audit.events[0].safe_metadata)
