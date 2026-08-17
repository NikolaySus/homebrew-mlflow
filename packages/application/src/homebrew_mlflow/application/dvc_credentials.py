from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    MachineScope,
    ProjectRole,
    PublicId,
    permits,
)

from .projects import AuthorizationDenied


@dataclass(frozen=True, slots=True)
class TemporaryS3Credential:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime


class DvcCredentialIssuer(Protocol):
    def issue(
        self, project_id: PublicId, read_only_object_keys: tuple[str, ...]
    ) -> TemporaryS3Credential: ...


class DvcCredentialAuthorization(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def shared_dvc_read_keys(
        self,
        project_id: PublicId,
        principal_id: PublicId,
        recovery_run_id: PublicId | None,
        at: datetime,
    ) -> tuple[str, ...]: ...


class DvcCredentialAudit(Protocol):
    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class DvcCredentialService:
    def __init__(
        self,
        authorization: DvcCredentialAuthorization,
        issuer: DvcCredentialIssuer,
        audit: DvcCredentialAudit | None = None,
    ) -> None:
        self._authorization = authorization
        self._issuer = issuer
        self._audit = audit

    def issue(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        recovery_run_id: PublicId | None = None,
        at: datetime | None = None,
        request_id: PublicId | None = None,
    ) -> TemporaryS3Credential:
        issued_at = at or datetime.now(UTC)
        role = self._authorization.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.DVC_TRANSFER):
            raise AuthorizationDenied("Contributor role is required for DVC transfer")
        read_keys = self._authorization.shared_dvc_read_keys(
            project_id, actor_id, recovery_run_id, issued_at
        )
        credential = self._issuer.issue(project_id, read_keys)
        if self._audit is not None and request_id is not None:
            self._audit.append_audit(
                AuditEvent(
                    actor_principal_id=actor_id,
                    action="dvc_credentials.issue",
                    resource_type="temporary_dvc_credentials",
                    resource_id=project_id,
                    outcome="success",
                    request_id=request_id,
                    project_id=project_id,
                    safe_metadata={
                        "expires_at": credential.expiration.isoformat(),
                        "shared_read_key_count": len(read_keys),
                        "recovery_run_id": (
                            str(recovery_run_id) if recovery_run_id is not None else None
                        ),
                    },
                    occurred_at=issued_at,
                )
            )
            self._audit.commit()
        return credential
