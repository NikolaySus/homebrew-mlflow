from datetime import UTC, datetime
from uuid import uuid4

from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import Base, GitLabMembershipReconciler
from homebrew_mlflow.infrastructure.database import (
    GitLabIdentityBindingRow,
    OrganizationRow,
    PrincipalRow,
    ProjectMembershipRow,
    ResearchProjectRow,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class GitLabResponse:
    def __init__(self, payload: object = None, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_gitlab_reconciler_adds_updates_and_removes_only_known_identities(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[tuple[str, str, object]] = []

    def get(url: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(("GET", url, kwargs.get("params")))
        if kwargs.get("params", {}).get("page") == "1":
            return GitLabResponse(
                [
                    {"id": 2, "access_level": 20},
                    {"id": 999, "access_level": 50},
                ],
                {"X-Next-Page": "2"},
            )
        return GitLabResponse(
            [
                {"id": 3, "access_level": 30},
            ]
        )

    def mutate(url: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((kwargs.pop("method", "MUTATE"), url, kwargs.get("data")))
        return GitLabResponse({})

    monkeypatch.setattr("homebrew_mlflow.infrastructure.gitlab_reconciliation.httpx.get", get)
    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.gitlab_reconciliation.httpx.post",
        lambda url, **kwargs: mutate(url, method="POST", **kwargs),
    )
    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.gitlab_reconciliation.httpx.put",
        lambda url, **kwargs: mutate(url, method="PUT", **kwargs),
    )
    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.gitlab_reconciliation.httpx.delete",
        lambda url, **kwargs: mutate(url, method="DELETE", **kwargs),
    )
    with Session(engine) as session:
        _seed(session)
        assert GitLabMembershipReconciler(
            session, base_url="https://git.example", access_token="safe-test-token"
        ).run_once(NOW)
        project = session.query(ResearchProjectRow).one()
        assert project.gitlab_reconciliation_state == "in_sync"

    assert [call[0] for call in calls] == ["GET", "GET", "POST", "PUT", "DELETE"]
    assert calls[1][2] == {"per_page": 100, "page": "2"}
    assert not any("999" in call[1] for call in calls if call[0] == "DELETE")


def _seed(session: Session) -> None:
    organization_key, project_key = uuid4(), uuid4()
    organization = PublicId.generate(ResourceKind.ORGANIZATION)
    project = PublicId.generate(ResourceKind.PROJECT)
    session.add(
        OrganizationRow(
            id=organization_key,
            public_id=str(organization),
            name="Research",
            created_at=NOW,
            archived_at=None,
        )
    )
    session.add(
        ResearchProjectRow(
            id=project_key,
            public_id=str(project),
            organization_id=organization_key,
            name="Project",
            slug="project",
            created_at=NOW,
            archived_at=None,
            state="active",
            gitlab_namespace_id="17",
            failure_code=None,
            updated_at=NOW,
            claimed_at=None,
            claimed_by=None,
            provisioning_attempt=1,
            gitlab_reconciliation_state="pending",
            gitlab_reconciliation_error=None,
            gitlab_reconciled_at=None,
            gitlab_reconcile_attempt=0,
        )
    )
    for subject, role in (("1", "viewer"), ("2", "contributor"), ("3", None)):
        principal_key = uuid4()
        principal = PublicId.generate(ResourceKind.PRINCIPAL)
        session.add(
            PrincipalRow(
                id=principal_key,
                public_id=str(principal),
                kind="human",
                display_name=f"User {subject}",
                created_at=NOW,
                archived_at=None,
            )
        )
        session.add(
            GitLabIdentityBindingRow(
                principal_id=principal_key,
                subject=subject,
                username=f"user{subject}",
                created_at=NOW,
                last_seen_at=NOW,
            )
        )
        if role is not None:
            session.add(
                ProjectMembershipRow(
                    project_id=project_key,
                    principal_id=principal_key,
                    role=role,
                    created_at=NOW,
                )
            )
    session.commit()
