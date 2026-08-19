from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    MlflowBrowserSessionService,
    NewMlflowBrowserSession,
    ResolvedMlflowBrowserSession,
)
from homebrew_mlflow.domain import ProjectRole, PublicId, ResourceKind


class SessionStore:
    def __init__(self) -> None:
        self.roles: dict[tuple[PublicId, PublicId], ProjectRole] = {}
        self.sessions: dict[str, NewMlflowBrowserSession] = {}
        self.revoked: set[PublicId] = set()

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return self.roles.get((project_id, principal_id))

    def add_mlflow_browser_session(self, session: NewMlflowBrowserSession) -> None:
        self.sessions[session.digest] = session

    def resolve_mlflow_browser_session(
        self, digest: str, now: datetime
    ) -> ResolvedMlflowBrowserSession | None:
        value = self.sessions.get(digest)
        if value is None or value.principal_id in self.revoked or value.expires_at <= now:
            return None
        return ResolvedMlflowBrowserSession(value.principal_id, value.default_project_id)

    def revoke_mlflow_browser_sessions(self, principal_id: PublicId, now: datetime) -> None:
        self.revoked.add(principal_id)


def test_mlflow_browser_session_rechecks_selected_project_membership() -> None:
    principal = PublicId.generate(ResourceKind.PRINCIPAL)
    initial = PublicId.generate(ResourceKind.PROJECT)
    selected = PublicId.generate(ResourceKind.PROJECT)
    store = SessionStore()
    store.roles[(initial, principal)] = ProjectRole.VIEWER
    store.roles[(selected, principal)] = ProjectRole.VIEWER
    now = datetime.now(UTC)
    service = MlflowBrowserSessionService(store, lifetime=timedelta(hours=12))

    token = service.issue(principal, initial, now)
    assert service.resolve(token, selected, now).default_project_id == selected

    del store.roles[(selected, principal)]
    with pytest.raises(AuthorizationDenied):
        service.resolve(token, selected, now)


def test_mlflow_browser_session_is_revocable() -> None:
    principal = PublicId.generate(ResourceKind.PRINCIPAL)
    project = PublicId.generate(ResourceKind.PROJECT)
    store = SessionStore()
    store.roles[(project, principal)] = ProjectRole.VIEWER
    now = datetime.now(UTC)
    service = MlflowBrowserSessionService(store)
    token = service.issue(principal, project, now)

    service.revoke_all(principal, now)

    with pytest.raises(AuthorizationDenied):
        service.resolve(token, None, now)
