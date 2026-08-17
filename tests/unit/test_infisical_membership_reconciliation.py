from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
from homebrew_mlflow.infrastructure.database import (
    Base,
    GitLabIdentityBindingRow,
    PrincipalRow,
    ProjectMembershipRow,
    ResearchProjectRow,
    SecretContextRow,
)
from homebrew_mlflow.infrastructure.infisical_reconciliation import (
    InfisicalMembershipReconciler,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)


def response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def test_reconciliation_invites_verified_gitlab_email(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    project_key = uuid4()
    principal_key = uuid4()
    invitations: list[dict[str, object]] = []

    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.infisical_reconciliation.httpx.get",
        lambda url, **_kwargs: response("GET", url, {"memberships": []}),
    )

    def invite(url: str, **kwargs: object) -> httpx.Response:
        invitations.append(kwargs["json"])  # type: ignore[arg-type]
        return response("POST", url, {})

    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.infisical_reconciliation.httpx.post", invite
    )

    with Session(engine) as session:
        session.add(
            PrincipalRow(
                id=principal_key,
                public_id="principal_01K00000000000000000000",
                kind="human",
                display_name="Researcher",
                created_at=NOW,
                archived_at=None,
            )
        )
        session.add(
            ResearchProjectRow(
                id=project_key,
                public_id="project_01K00000000000000000000",
                organization_id=uuid4(),
                name="Protein Folding",
                slug="protein-folding",
                created_at=NOW,
                archived_at=None,
                state="active",
                gitlab_namespace_id="1",
                failure_code=None,
                updated_at=NOW,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
            )
        )
        session.flush()
        session.add_all(
            (
                GitLabIdentityBindingRow(
                    principal_id=principal_key,
                    subject="42",
                    username="researcher",
                    email="researcher@example.com",
                    created_at=NOW,
                    last_seen_at=NOW,
                ),
                ProjectMembershipRow(
                    project_id=project_key,
                    principal_id=principal_key,
                    role="contributor",
                    created_at=NOW,
                ),
                SecretContextRow(
                    project_id=project_key,
                    infisical_project_id="infisical-project",
                    environment_slug="dev",
                    secret_path="/",
                    updated_at=NOW,
                    reconciliation_state="queued",
                    last_error_code=None,
                    last_reconciled_at=None,
                    reconcile_attempt=0,
                ),
            )
        )
        session.commit()

        worked = InfisicalMembershipReconciler(
            session, base_url="https://secrets.example", access_token="token"
        ).run_once(NOW)

    assert worked
    assert invitations == [
        {
            "emails": ["researcher@example.com"],
            "usernames": [],
            "roleSlugs": ["member"],
        }
    ]
