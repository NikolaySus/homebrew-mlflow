from datetime import UTC, datetime
from http.cookies import SimpleCookie
from uuid import uuid4

from fastapi.testclient import TestClient
from homebrew_mlflow.api.main import create_app
from homebrew_mlflow.api.security import access_tokens
from homebrew_mlflow.application import TokenAudience
from homebrew_mlflow.domain import MachineScope, PublicId, ResourceKind
from homebrew_mlflow.infrastructure.database import (
    Base,
    MlflowBrowserSessionRow,
    OrganizationRow,
    PrincipalRow,
    ProjectMembershipRow,
    ResearchProjectRow,
)
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_gateway_session_mints_project_read_token_and_rechecks_membership(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    principal_id = PublicId.generate(ResourceKind.PRINCIPAL)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    principal_key, organization_key, project_key = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            OrganizationRow(
                id=organization_key,
                public_id=str(PublicId.generate(ResourceKind.ORGANIZATION)),
                name="Research",
                created_at=now,
                archived_at=None,
            )
        )
        session.add(
            PrincipalRow(
                id=principal_key,
                public_id=str(principal_id),
                kind="human",
                display_name="Researcher",
                created_at=now,
                archived_at=None,
            )
        )
        session.add(
            ResearchProjectRow(
                id=project_key,
                public_id=str(project_id),
                organization_id=organization_key,
                name="Models",
                slug="models",
                created_at=now,
                archived_at=None,
                state="active",
                gitlab_namespace_id="7",
                failure_code=None,
                updated_at=now,
                claimed_at=None,
                claimed_by=None,
                provisioning_attempt=0,
                gitlab_reconciliation_state="active",
                gitlab_reconciliation_error=None,
                gitlab_reconciled_at=now,
                gitlab_reconcile_attempt=0,
            )
        )
        session.add(
            ProjectMembershipRow(
                project_id=project_key,
                principal_id=principal_key,
                role="viewer",
                created_at=now,
            )
        )
        session.commit()

    monkeypatch.setattr("homebrew_mlflow.api.auth.create_session", lambda _url: Session(engine))
    monkeypatch.setattr(
        "homebrew_mlflow.api.mlflow_compat.create_session", lambda _url: Session(engine)
    )
    client = TestClient(create_app())
    platform_token = access_tokens().issue(principal_id, TokenAudience.PLATFORM_API)
    created = client.post(
        "/api/v1/auth/mlflow/session",
        headers={"Authorization": f"Bearer {platform_token}"},
        json={"project_id": str(project_id)},
    )
    assert created.status_code == 200
    assert created.json()["workspace_url"].startswith("/mlflow/?workspace=pr-")
    cookie = SimpleCookie()
    cookie.load(created.headers["set-cookie"])
    browser_token = cookie["hm_mlflow_session"].value
    assert cookie["hm_mlflow_session"]["httponly"]
    assert cookie["hm_mlflow_session"]["path"] == "/mlflow"
    with Session(engine) as session:
        stored = session.scalar(select(MlflowBrowserSessionRow))
        assert stored is not None
        assert stored.digest != browser_token

    authorized = client.get(
        "/api/v1/auth/mlflow/authorize",
        headers={"Cookie": f"hm_mlflow_session={browser_token}"},
    )
    assert authorized.status_code == 204
    assert authorized.headers["x-mlflow-workspace"].startswith("pr-")
    claims = access_tokens().verify(
        authorized.headers["authorization"].removeprefix("Bearer "),
        TokenAudience.MLFLOW,
    )
    assert claims.project_id == project_id
    assert claims.scopes == frozenset({MachineScope.READ})

    tracking_token = access_tokens().issue(
        principal_id,
        TokenAudience.MLFLOW,
        project_id=project_id,
        run_id=PublicId.generate(ResourceKind.RUN),
        scopes=frozenset({MachineScope.TRACK}),
    )
    tracking_headers = {"Authorization": f"Bearer {tracking_token}"}
    workspaces = client.get("/api/v1/mlflow/workspaces", headers=tracking_headers)
    assert workspaces.status_code == 200
    assert [workspace["project_id"] for workspace in workspaces.json()] == [str(project_id)]
    snapshot = client.get(
        f"/api/v1/mlflow/workspaces/{workspaces.json()[0]['name']}/snapshot",
        headers=tracking_headers,
    )
    assert snapshot.status_code == 403

    with Session(engine) as session:
        session.execute(delete(ProjectMembershipRow))
        session.commit()
    forbidden = client.get(
        "/api/v1/auth/mlflow/authorize",
        headers={"Cookie": f"hm_mlflow_session={browser_token}"},
    )
    assert forbidden.status_code == 403
