from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    MachineScope,
    Principal,
    PrincipalKind,
    ProjectMembership,
    ProjectRole,
    PublicId,
    ResourceKind,
    permits,
)

from .projects import AuthorizationDenied


@dataclass(frozen=True, slots=True)
class StoredMachineCredential:
    id: PublicId
    principal_id: PublicId
    project_id: PublicId
    digest: str
    scopes: frozenset[MachineScope]
    created_at: datetime
    expires_at: datetime
    revoked: bool


@dataclass(frozen=True, slots=True)
class CreatedMachineCredential:
    id: PublicId
    principal_id: PublicId
    project_id: PublicId
    secret: str
    scopes: frozenset[MachineScope]
    expires_at: datetime


class MachineCredentialStore(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def add_machine_credential(
        self,
        principal: Principal,
        membership: ProjectMembership,
        credential_id: PublicId,
        digest: str,
        scopes: frozenset[MachineScope],
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    def machine_credential(self, credential_id: PublicId) -> StoredMachineCredential | None: ...

    def machine_credentials(self, project_id: PublicId) -> tuple[StoredMachineCredential, ...]: ...

    def revoke_machine_credential(self, credential_id: PublicId, revoked_at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class MachineCredentialService:
    def __init__(self, store: MachineCredentialStore) -> None:
        self._store = store

    def create(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        display_name: str,
        role: ProjectRole,
        scopes: frozenset[MachineScope],
        now: datetime,
        request_id: PublicId | None = None,
    ) -> CreatedMachineCredential:
        if self._store.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to create machine credentials")
        if role not in {ProjectRole.VIEWER, ProjectRole.CONTRIBUTOR}:
            raise ValueError("machine principals may only be Viewer or Contributor")
        if not scopes or any(not permits(role, scope) for scope in scopes):
            raise ValueError("machine credential scopes exceed its project role")
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("machine principal display name is required")
        principal = Principal(
            PublicId.generate(ResourceKind.PRINCIPAL),
            PrincipalKind.MACHINE,
            normalized_name,
            now,
        )
        membership = ProjectMembership.create(
            project_id, principal, role, belongs_to_organization=True
        )
        credential_id = PublicId.generate(ResourceKind.MACHINE_CREDENTIAL)
        secret = "hmmc_" + secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=90)
        self._store.add_machine_credential(
            principal,
            membership,
            credential_id,
            hashlib.sha256(secret.encode("ascii")).hexdigest(),
            scopes,
            now,
            expires_at,
        )
        self._audit(
            actor_id,
            project_id,
            "machine_credential.create",
            credential_id,
            request_id,
            now,
            {"principal_id": str(principal.id), "role": role.value},
        )
        self._store.commit()
        return CreatedMachineCredential(
            credential_id, principal.id, project_id, secret, scopes, expires_at
        )

    def list(
        self, actor_id: PublicId, project_id: PublicId
    ) -> tuple[StoredMachineCredential, ...]:
        if self._store.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to list machine credentials")
        return self._store.machine_credentials(project_id)

    def revoke(
        self,
        actor_id: PublicId,
        credential_id: PublicId,
        now: datetime,
        request_id: PublicId | None = None,
    ) -> StoredMachineCredential:
        record = self._store.machine_credential(credential_id)
        if record is None:
            raise ValueError("machine credential does not exist")
        if self._store.project_role(record.project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to revoke machine credentials")
        if not record.revoked:
            self._store.revoke_machine_credential(credential_id, now)
            self._audit(
                actor_id,
                record.project_id,
                "machine_credential.revoke",
                credential_id,
                request_id,
                now,
                {"principal_id": str(record.principal_id)},
            )
            self._store.commit()
        return StoredMachineCredential(
            record.id,
            record.principal_id,
            record.project_id,
            record.digest,
            record.scopes,
            record.created_at,
            record.expires_at,
            True,
        )

    def authenticate(
        self,
        credential_id: PublicId,
        secret: str,
        now: datetime | None = None,
        request_id: PublicId | None = None,
    ) -> StoredMachineCredential:
        record = self._store.machine_credential(credential_id)
        presented = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if (
            record is None
            or record.revoked
            or record.expires_at <= (now or datetime.now(UTC))
            or not hmac.compare_digest(record.digest, presented)
            or self._store.project_role(record.project_id, record.principal_id) is None
        ):
            raise AuthorizationDenied("machine credential is invalid")
        self._audit(
            record.principal_id,
            record.project_id,
            "authentication.machine",
            credential_id,
            request_id,
            now or datetime.now(UTC),
            {},
        )
        self._store.commit()
        return record

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
        self._store.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=action,
                resource_type="machine_credential",
                resource_id=resource_id,
                outcome="success",
                request_id=request_id or PublicId.generate(ResourceKind.REQUEST),
                project_id=project_id,
                safe_metadata=metadata,
                occurred_at=now,
            )
        )
