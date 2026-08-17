from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from homebrew_mlflow.api.main import create_app
from homebrew_mlflow.api.security import access_tokens
from homebrew_mlflow.application import (
    HostedNamespace,
    HostedRepository,
    ProjectProvisioningCoordinator,
    TemporaryS3Credential,
    TokenAudience,
)
from homebrew_mlflow.domain import Principal, PrincipalKind
from homebrew_mlflow.infrastructure import FileSystemRepositoryTemplate
from homebrew_mlflow.infrastructure.database import (
    Base,
    GitRepositoryRow,
    PrincipalRow,
    ResearchProjectRow,
    RunMetricRow,
    RunParameterRow,
    RunTagRow,
    SqlAlchemyProvisioningStore,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


class NamespaceHost:
    def create_private(self, _name: str, _slug: str) -> HostedNamespace:
        return HostedNamespace("7", "protein-folding")


class RepositoryHost:
    def create_with_seed(self, request, files) -> HostedRepository:  # type: ignore[no-untyped-def]
        assert request.namespace_id == 7
        assert any(file.path == "AGENTS.md" for file in files)
        return HostedRepository(
            "9",
            "main",
            "https://git.example/protein-folding/protein-folding",
            "https://git.example/protein-folding/protein-folding.git",
            "git@git.example:protein-folding/protein-folding.git",
        )


class CredentialIssuer:
    def issue(self, _project_id, _read_only_object_keys):  # type: ignore[no-untyped-def]
        return TemporaryS3Credential(
            "temporary-access",
            "temporary-secret",
            "temporary-session",
            datetime.now(UTC),
        )


def test_authenticated_user_claims_installation_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    principal = Principal.create(PrincipalKind.HUMAN, "Administrator")
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            PrincipalRow(
                id=uuid4(),
                public_id=str(principal.id),
                kind=principal.kind.value,
                display_name=principal.display_name,
                created_at=now,
                archived_at=None,
            )
        )
        session.commit()

    monkeypatch.setattr("homebrew_mlflow.api.setup.create_session", lambda _url: Session(engine))
    monkeypatch.setattr("homebrew_mlflow.api.auth.create_session", lambda _url: Session(engine))
    monkeypatch.setattr("homebrew_mlflow.api.projects.create_session", lambda _url: Session(engine))
    monkeypatch.setattr("homebrew_mlflow.api.runs.create_session", lambda _url: Session(engine))
    monkeypatch.setattr(
        "homebrew_mlflow.api.artifacts.create_session", lambda _url: Session(engine)
    )
    monkeypatch.setattr(
        "homebrew_mlflow.api.publications.create_session", lambda _url: Session(engine)
    )
    monkeypatch.setattr("homebrew_mlflow.api.tracking.create_session", lambda _url: Session(engine))
    monkeypatch.setattr(
        "homebrew_mlflow.api.dvc_credentials.create_session", lambda _url: Session(engine)
    )
    monkeypatch.setattr(
        "homebrew_mlflow.api.dvc_credentials.credential_issuer",
        lambda *_args: CredentialIssuer(),
    )
    token = access_tokens().issue(principal.id, TokenAudience.PLATFORM_API)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(create_app())

    status_before = client.get("/api/v1/setup/status", headers=headers)
    assert status_before.status_code == 200
    assert status_before.json() == {"claimed": False}

    response = client.post(
        "/api/v1/setup/claim",
        headers=headers,
        json={
            "organization_name": "Research",
            "bootstrap_token": "development-one-time-bootstrap-token",
        },
    )

    assert response.status_code == 200
    assert response.json()["principal_id"] == str(principal.id)
    organization_id = response.json()["organization_id"]
    assert client.get("/api/v1/setup/status", headers=headers).json() == {"claimed": True}

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "organization_id": organization_id,
            "name": "Protein Folding",
            "slug": "protein-folding",
        },
    )
    assert project.status_code == 202
    assert project.json()["default_repository"]["state"] == "provisioning"
    assert project.json()["default_repository"]["slug"] == "protein-folding"
    repository_id = project.json()["default_repository"]["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchProjectRow)) == 1
        assert session.scalar(select(func.count()).select_from(GitRepositoryRow)) == 1
        project_row = session.scalar(select(ResearchProjectRow))
        repository_row = session.scalar(select(GitRepositoryRow))
        assert project_row is not None and repository_row is not None
        project_row.state = "failed"
        project_row.failure_code = "template_commit_failed"
        repository_row.state = "failed"
        repository_row.failure_code = "template_commit_failed"
        repository_row.provider_id = "9"
        session.commit()

    retry = client.post(
        f"/api/v1/projects/{project.json()['id']}/repositories/"
        f"{repository_id}/retry-provisioning",
        headers=headers,
    )
    assert retry.status_code == 202
    assert retry.json()["state"] == "provisioning"
    assert retry.json()["failure_code"] is None

    with Session(engine) as session:
        coordinator = ProjectProvisioningCoordinator(
            SqlAlchemyProvisioningStore(session),
            NamespaceHost(),
            RepositoryHost(),
            FileSystemRepositoryTemplate(Path(__file__).parents[2] / "repository_template"),
            platform_url="https://ml.example",
            dvc_remote_base_url="s3://research/dvc",
            s3_endpoint_url="https://objects.example",
        )
        assert coordinator.run_once("integration-test")
    with Session(engine) as session:
        stored_project = session.scalar(select(ResearchProjectRow))
        stored_repository = session.scalar(select(GitRepositoryRow))
        assert stored_project is not None and stored_project.state == "active"
        assert stored_project.gitlab_namespace_id == "7"
        assert stored_repository is not None and stored_repository.state == "active"
        assert stored_repository.provider_id == "9"

    run = client.post(
        f"/api/v1/projects/{project.json()['id']}/runs",
        headers=headers,
        json={
            "repository_id": project.json()["default_repository"]["id"],
            "experiment_name": "baseline",
            "command": ["python", "train.py"],
        },
    )
    assert run.status_code == 200
    assert run.json()["state"] == "running"
    exchanged = client.post(
        "/api/v1/auth/exchange",
        headers=headers,
        json={
            "audience": "dvc-credentials",
            "project_id": project.json()["id"],
            "scopes": ["dvc_transfer"],
        },
    )
    assert exchanged.status_code == 200
    temporary = client.post(
        f"/api/v1/projects/{project.json()['id']}/dvc-credentials",
        headers={"Authorization": f"Bearer {exchanged.json()['access_token']}"},
    )
    assert temporary.status_code == 200
    assert set(temporary.json()) == {
        "Version",
        "AccessKeyId",
        "SecretAccessKey",
        "SessionToken",
        "Expiration",
    }
    artifact = client.post(
        f"/api/v1/projects/{project.json()['id']}/artifacts",
        headers=headers,
        json={"name": "model"},
    )
    assert artifact.status_code == 201
    publication_exchange = client.post(
        "/api/v1/auth/exchange",
        headers=headers,
        json={
            "audience": "publication",
            "project_id": project.json()["id"],
            "scopes": ["publish"],
        },
    )
    publication_headers = {
        "Authorization": f"Bearer {publication_exchange.json()['access_token']}",
        "Idempotency-Key": "acceptance-publication",
    }
    publication_payload = {
        "artifact_id": artifact.json()["id"],
        "repository_id": project.json()["default_repository"]["id"],
        "commit_sha": "a" * 40,
        "selector": {
            "kind": "pipeline-output",
            "pipeline_file": "dvc.yaml",
            "stage": "train",
            "output": "model.bin",
        },
        "run_id": run.json()["id"],
        "client": {"name": "acceptance", "version": "0.1.0"},
    }
    publication = client.post(
        f"/api/v1/projects/{project.json()['id']}/publication-operations",
        headers=publication_headers,
        json=publication_payload,
    )
    replay = client.post(
        f"/api/v1/projects/{project.json()['id']}/publication-operations",
        headers=publication_headers,
        json=publication_payload,
    )
    assert publication.status_code == 202
    assert replay.json()["operation_id"] == publication.json()["operation_id"]
    status_response = client.get(publication.json()["status_url"], headers=publication_headers)
    assert status_response.json()["state"] == "queued"
    logging_headers = {"Authorization": f"Bearer {run.json()['logging_token']}"}
    tracked = client.post(
        f"/api/v1/runs/{run.json()['id']}/tracking/batch",
        headers=logging_headers,
        json={
            "parameters": [{"key": "seed", "value": "42"}],
            "metrics": [
                {"key": "loss", "value": 0.5, "timestamp_ms": 1000, "step": 1},
                {"key": "loss", "value": 0.25, "timestamp_ms": 2000, "step": 2},
            ],
            "tags": [{"key": "model", "value": "resnet"}],
        },
    )
    assert tracked.status_code == 204
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RunParameterRow)) == 1
        assert session.scalar(select(func.count()).select_from(RunMetricRow)) == 2
        assert session.scalar(select(func.count()).select_from(RunTagRow)) == 1
    heartbeat = client.post(f"/api/v1/runs/{run.json()['id']}/heartbeat", headers=headers)
    assert heartbeat.status_code == 200
    finalized = client.post(
        f"/api/v1/runs/{run.json()['id']}/finalize",
        headers=headers,
        json={
            "exit_code": 0,
            "status": "succeeded",
            "git_commit_sha": "a" * 40,
            "evidence": {"dvc": {"revision": "exp-1"}},
        },
    )
    assert finalized.status_code == 200
    assert finalized.json()["state"] == "succeeded"

    repeated = client.post(
        "/api/v1/setup/claim",
        headers=headers,
        json={
            "organization_name": "Other",
            "bootstrap_token": "development-one-time-bootstrap-token",
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "resource_conflict"
