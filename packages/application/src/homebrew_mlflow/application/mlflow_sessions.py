from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from homebrew_mlflow.domain import MachineScope, ProjectRole, PublicId, permits

from .projects import AuthorizationDenied


@dataclass(frozen=True, slots=True)
class NewMlflowBrowserSession:
    digest: str
    principal_id: PublicId
    default_project_id: PublicId
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedMlflowBrowserSession:
    principal_id: PublicId
    default_project_id: PublicId


class MlflowBrowserSessionStore(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def add_mlflow_browser_session(self, session: NewMlflowBrowserSession) -> None: ...

    def resolve_mlflow_browser_session(
        self, digest: str, now: datetime
    ) -> ResolvedMlflowBrowserSession | None: ...

    def revoke_mlflow_browser_sessions(self, principal_id: PublicId, now: datetime) -> None: ...


class MlflowBrowserSessionService:
    def __init__(
        self,
        store: MlflowBrowserSessionStore,
        lifetime: timedelta = timedelta(hours=12),
    ) -> None:
        self._store = store
        self._lifetime = lifetime

    def issue(
        self,
        principal_id: PublicId,
        default_project_id: PublicId,
        now: datetime | None = None,
    ) -> str:
        role = self._store.project_role(default_project_id, principal_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        issued_at = now or datetime.now(UTC)
        token = "hmms_" + secrets.token_urlsafe(32)
        self._store.add_mlflow_browser_session(
            NewMlflowBrowserSession(
                self._digest(token),
                principal_id,
                default_project_id,
                issued_at,
                issued_at + self._lifetime,
            )
        )
        return token

    def resolve(
        self,
        token: str,
        selected_project_id: PublicId | None,
        now: datetime | None = None,
    ) -> ResolvedMlflowBrowserSession:
        if not token.startswith("hmms_"):
            raise AuthorizationDenied("invalid MLflow browser session")
        resolved = self._store.resolve_mlflow_browser_session(
            self._digest(token), now or datetime.now(UTC)
        )
        if resolved is None:
            raise AuthorizationDenied("MLflow browser session is expired or revoked")
        project_id = selected_project_id or resolved.default_project_id
        role = self._store.project_role(project_id, resolved.principal_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return ResolvedMlflowBrowserSession(resolved.principal_id, project_id)

    def revoke_all(self, principal_id: PublicId, now: datetime | None = None) -> None:
        self._store.revoke_mlflow_browser_sessions(
            principal_id, now or datetime.now(UTC)
        )

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
